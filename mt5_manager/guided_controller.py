"""Prepared candidates use the existing persistent FIFO and full stage pipeline."""
from __future__ import annotations
import json
import time
from . import guided_batches as batches


class GuidedControllerMixin:
    def submit_guided(self, submission):
        with self.lock:
            package, launch_options = batches.unpack_submission(submission)
            project = self.config['project_dir']
            broker = str(self.config['broker']).upper()
            account = str(self.config['account_type']).upper()
            directory = batches.store_batch(project,package,broker,account)
            batch_id = package['batch_id']
            receipt = directory/'receipt.json'
            options_path = directory/'launch_options.json'
            queued = any((q.get('payload') or {}).get('guided_batch_id')==batch_id for q in self.queue)
            current = (self.state.get('request') or {}).get('guided_batch_id')==batch_id
            duplicate = receipt.exists() or queued or current or bool(batches.read_run(project,batch_id))
            if options_path.exists():
                stored_options = json.loads(options_path.read_text(encoding='utf-8'))
                if launch_options is not None and stored_options != launch_options:
                    raise ValueError('El lote ya está ligado a otra configuración de ejecución')
                launch_options = stored_options
            elif launch_options is not None:
                if duplicate:
                    raise ValueError('Un lote ya recibido no puede adquirir otra configuración de ejecución')
                batches.save_json(options_path,launch_options)
            if duplicate:
                return {'batch_id':batch_id,'duplicate':True,'status':self.guided_status(batch_id)}
            request = self._normalize_generation({**{
                'guided_batch_id':batch_id,'generation_mode':'discovery','cycles':1,'generations':1,
                'variants_per_seed':1,'max_seeds':len(package['candidates']),
                'execute_backtests':True,'dry_run':False,'continue_last':False,
                'run_robustness':True,'run_final_tick':True,'run_final_tick_6m':True,
                'repair_after_generation':False,'cleanup_after_run':False}, **(launch_options or {})})
            # Persist in the ordinary queue before acknowledging receipt. A retry
            # after a lost HTTP response finds the same batch and never relaunches it.
            value = self._enqueue('generation',request,f"Guiado {batch_id[:12]} · {len(package['candidates'])} candidatos")
            batches.save_json(receipt,{'status':'queued','batch_id':batch_id,'launch_options':launch_options or {},
                                      'queue_id':value['queue_item']['id'],'stage_wall_seconds':{}})
            self._schedule_queue_drain()
            return {'batch_id':batch_id,'duplicate':False,'queued':True,'queue_id':value['queue_item']['id']}

    def guided_status(self, batch_id):
        from .node import read_settings, memory_path
        from pathlib import Path
        with self.lock:
            project = self.config['project_dir']
            settings = Path(str(self.config.get('settings_file') or 'ui_settings.ini'))
            if not settings.is_absolute():
                settings = Path(project)/settings
            value = batches.results(project,batch_id,memory_path(self.config,read_settings(settings)))
            if (self.state.get('request') or {}).get('guided_batch_id')==batch_id:
                value['receipt']['status'] = self.state.get('status')
                value['receipt']['current_stage'] = self.state.get('current_stage')
            elif any((q.get('payload') or {}).get('guided_batch_id')==batch_id for q in self.queue):
                value['receipt']['status']='queued'
            elif value['receipt']['status']=='queued':
                value['receipt']['status']='cancelled_or_interrupted'
            return value

    def guided_stage_started(self):
        if (self.state.get('request') or {}).get('guided_batch_id'):
            self._guided_stage_clock = time.monotonic()

    def guided_stage_finished(self, stage):
        batch_id = (self.state.get('request') or {}).get('guided_batch_id')
        if not batch_id:
            return
        path = batches.batch_dir(self.config['project_dir'],batch_id)/'receipt.json'
        receipt = json.loads(path.read_text()) if path.exists() else {'batch_id':batch_id}
        seconds = max(0.0,time.monotonic()-getattr(self,'_guided_stage_clock',time.monotonic()))
        timings = receipt.setdefault('stage_wall_seconds',{})
        timings[stage] = timings.get(stage,0.0)+seconds
        receipt['status']='running'
        batches.save_json(path,receipt)

    def guided_completed(self):
        batch_id = (self.state.get('request') or {}).get('guided_batch_id')
        if not batch_id:
            return
        path = batches.batch_dir(self.config['project_dir'],batch_id)/'receipt.json'
        receipt = json.loads(path.read_text()) if path.exists() else {'batch_id':batch_id}
        receipt.update(status=self.state['status'],return_code=self.state.get('return_code'),
                       stage_return_codes=self.state.get('stage_return_codes',{}))
        batches.save_json(path,receipt)
