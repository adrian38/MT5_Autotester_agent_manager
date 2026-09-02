"""Portable prepared-batch protocol. Also shipped in the agent's embedded node."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

MAX_BODY = 16_000_000
MAX_SET = 256_000
MAX_CANDIDATES = 200


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def normalized(value):
    value = str(value).strip()
    try:
        n = Decimal(value)
        if n.is_finite():
            text = format(n, 'f')
            return (text.rstrip('0').rstrip('.') if '.' in text else text) if n else '0'
    except InvalidOperation:
        pass
    return value.lower() if value.lower() in {'true','false'} else value


def set_text(raw: bytes) -> str:
    for encoding in (['utf-16'] if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else ['utf-8-sig','cp1252']):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError('Codificación .set inválida')


def set_params(raw: bytes) -> dict:
    params = {}
    for line in set_text(raw).splitlines():
        if '=' not in line or line.lstrip().startswith(';'):
            continue
        key, value = line.split('=',1)
        key = key.strip()
        if not key or key in params:
            raise ValueError('Parámetro vacío o duplicado en .set')
        params[key] = value.split('||',1)[0].strip()
    return params


def replace_current(text, key, value):
    lines = text.splitlines(keepends=True)
    for i,line in enumerate(lines):
        if '=' in line and not line.lstrip().startswith(';') and line.split('=',1)[0].strip()==key:
            lhs, raw = line.split('=',1)
            ending = '\r\n' if raw.endswith('\r\n') else '\n' if raw.endswith('\n') else ''
            parts = raw.rstrip('\r\n').split('||')
            parts[0] = str(value)
            lines[i] = lhs+'='+'||'.join(parts)+ending
            return ''.join(lines)
    raise ValueError('Parámetro de mutación ausente')


def fingerprint(broker, account, symbol, period, params):
    values = {k:normalized(v) for k,v in params.items()}
    values['ForceSymbol'] = symbol.upper()
    return digest(json.dumps([f'{broker}_{account}',symbol.upper(),period.upper(),sorted(values.items())],ensure_ascii=False).encode())


def batch_identity(package):
    body = {k:v for k,v in package.items() if k!='batch_id'}
    return digest(json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())


def batch_dir(project, batch_id):
    if not isinstance(batch_id,str) or not re.fullmatch('[a-f0-9]{64}',batch_id):
        raise ValueError('Identificador de lote inválido')
    project = Path(project).resolve()
    path = (project/'outputs/guided_batches'/batch_id).resolve()
    return assert_writable(path, project)


def assert_writable(path, project):
    """Embedded node guard: never accept sender paths or escape this project."""
    path, project = Path(path).resolve(), Path(project).resolve()
    if not path.is_relative_to(project):
        raise ValueError('Destino fuera del proyecto del nodo')
    return path


def validate_package(package, broker, account):
    if not isinstance(package,dict) or set(package)!={'version','batch_id','broker','account_type','candidates'}:
        raise ValueError('Contrato de lote inválido')
    if type(package['version']) is not int or package['version']!=1 or package['broker']!=broker or package['account_type']!=account:
        raise ValueError('Versión, broker o cuenta incorrectos')
    if package['batch_id']!=batch_identity(package):
        raise ValueError('Hash del lote incorrecto')
    candidates = package['candidates']
    if not isinstance(candidates,list) or not 1<=len(candidates)<=MAX_CANDIDATES:
        raise ValueError('Cantidad de candidatos inválida')
    seen, decoded = set(), []
    fields = {'fingerprint','family','target_symbol','period','mode','root_seed','parent_candidate_id',
              'mutation','set_sha256','parent_sha256','set_b64','parent_b64'}
    for item in candidates:
        if not isinstance(item,dict) or set(item)!=fields:
            raise ValueError('Contrato de candidato inválido')
        if not isinstance(item['parent_candidate_id'],int) or isinstance(item['parent_candidate_id'],bool) or item['parent_candidate_id']<=0:
            raise ValueError('Padre inválido')
        if any(not isinstance(item[k],str) or not item[k] or len(item[k])>2048 for k in ('family','target_symbol','period','root_seed')):
            raise ValueError('Metadatos inválidos')
        if item['mode'] not in {'guided','exploration'} or not re.fullmatch(r'(M[1-9][0-9]*|H[1-9][0-9]*|D1|W1|MN1)',item['period']):
            raise ValueError('Modo o timeframe inválido')
        raw = []
        for prefix in ('set','parent'):
            encoded = item[prefix+'_b64']
            if not isinstance(encoded,str) or len(encoded)>MAX_SET*2:
                raise ValueError('Set demasiado grande')
            content = base64.b64decode(encoded,validate=True)
            if not content or len(content)>MAX_SET or digest(content)!=item[prefix+'_sha256']:
                raise ValueError('Hash/tamaño de set incorrecto')
            raw.append(content)
        values, previous = set_params(raw[0]), set_params(raw[1])
        change = item['mutation']
        if not isinstance(change,dict) or not {'key','old','new','step','direction','minimum','maximum'}<=set(change) or not isinstance(change['key'],str):
            raise ValueError('Mutación inválida')
        try:
            old,new,step,minimum,maximum = (Decimal(str(change[k])) for k in ('old','new','step','minimum','maximum'))
            valid = all(v.is_finite() for v in (old,new,step,minimum,maximum)) and step>0 and minimum<maximum
            valid = valid and change['direction'] in (-1,1) and new-old==step*change['direction'] and minimum<=new<=maximum
        except (InvalidOperation,TypeError,ValueError):
            valid = False
        if not valid:
            raise ValueError('Paso/rango/dirección de mutación inválidos')
        key = change['key']
        if set(values)!=set(previous) or [k for k in sorted(values) if normalized(values[k])!=normalized(previous[k])]!=[key]:
            raise ValueError('Se exige exactamente una mutación')
        if normalized(previous[key])!=normalized(change['old']) or normalized(values[key])!=normalized(change['new']):
            raise ValueError('Valores de mutación incoherentes')
        if set_text(raw[0])!=replace_current(set_text(raw[1]),key,change['new']):
            raise ValueError('El lote alteró parámetros/rangos fuera de la mutación declarada')
        for name,expected in {'Risk':'0','StartLots':'0.01','AdjustLotsizeToVariableValues':'false','UseEveryTick':'false'}.items():
            if normalized(values.get(name,''))!=expected:
                raise ValueError('Set incompatible con seguridad/base OHLC')
        if values.get('ForceSymbol','').upper()!=item['target_symbol'].upper():
            raise ValueError('ForceSymbol incorrecto')
        fp = fingerprint(broker,account,item['target_symbol'],item['period'],values)
        if fp!=item['fingerprint'] or fp in seen:
            raise ValueError('Fingerprint incorrecto o duplicado')
        seen.add(fp)
        decoded.append((item,raw[0],raw[1]))
    return decoded


def save_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)


def store_batch(project, package, broker, account):
    decoded = validate_package(package,broker,account)
    directory = batch_dir(project,package['batch_id'])
    marker = directory/'batch.json'
    if marker.exists():
        return directory
    directory.mkdir(parents=True,exist_ok=True)
    records = []
    for item,raw,parent in decoded:
        name = item['fingerprint']
        for suffix,content in (('.set',raw),('.parent.set',parent)):
            target = (directory/(name+suffix)).resolve()
            if not target.is_relative_to(directory):
                raise ValueError('Ruta de archivo fuera del inbox')
            target.write_bytes(content)
        records.append({k:v for k,v in item.items() if k not in {'set_b64','parent_b64'}})
    save_json(marker,{'version':1,'batch_id':package['batch_id'],'broker':broker,'account_type':account,'candidates':records})
    return directory


def read_run(project, batch_id):
    path = batch_dir(project,batch_id)/'run.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def results(project, batch_id, database):
    directory = batch_dir(project,batch_id)
    batch = json.loads((directory/'batch.json').read_text(encoding='utf-8'))
    receipt_path = directory/'receipt.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {'status':'received'}
    run = read_run(project,batch_id)
    output = {'batch_id':batch_id,'broker':batch['broker'],'account_type':batch['account_type'],
              'receipt':receipt,'run_id':run.get('run_id') if run else None,'candidates':[],
              'timing_scope':'stage wall seconds for this batch; not per-candidate compute hours'}
    if not run:
        return output
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        for item in batch['candidates']:
            candidate_id = run.get('candidate_ids',{}).get(item['fingerprint'])
            row = conn.execute('''select c.id,c.status base,r.status robustness,p.status probe,f.status final_6m
                from candidates c left join candidate_robustness r on r.candidate_id=c.id
                left join candidate_final_tick p on p.candidate_id=c.id
                left join candidate_final_tick_6m f on f.candidate_id=c.id
                where c.id=? and c.run_id=?''',(candidate_id,run['run_id'])).fetchone()
            output['candidates'].append({'fingerprint':item['fingerprint'],
                **(dict(row) if row else {'id':candidate_id,'base':None,'robustness':None,'probe':None,'final_6m':None}),
                'positive':bool(row and row['final_6m']=='accepted')})
    output['positives'] = sum(r['positive'] for r in output['candidates'])
    return output
