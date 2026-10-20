# El https del manager y el acceso desde fuera de casa

Al poner el certificado local (2026-10-19) el manager dejó de ser alcanzable
desde la calle. No fue el router ni el DDNS: el reenvío `8750 → 192.168.1.152`
seguía intacto y el nombre resolvía bien. Fueron dos cosas del certificado, y
las dos callan.

## 1. El puerto ya no habla `http`, y no lo dice

`ManagerServer.get_request` envuelve **toda** conexión aceptada en cuanto
encuentra el certificado (`mt5_manager/manager_tls.py`). No hay redirección ni
respuesta de cortesía: un `GET` en claro contra el puerto TLS se cae en el
saludo y el cliente ve «empty reply from server», igual que si el manager
estuviera apagado.

```
curl http://ringaping.ddns.net:8750/api/pulse   -> 000, empty reply
curl -k https://ringaping.ddns.net:8750/api/pulse -> 200
```

Cualquier atajo, marcador o sonda guardada con `http://` deja de funcionar al
reiniciar. El `README.md` ya avisaba del vigilante externo de `/api/pulse`; el
móvil es el mismo caso.

## 2. El certificado no incluye el nombre de fuera

`tools.make_local_cert` sólo mete sola la parte que la máquina sabe deducir:
`localhost`, el nombre del equipo y sus IP **privadas**. Es deliberado —el
nombre del equipo llegó a resolver a dos IP públicas de AWS—, pero deja fuera
el dominio dinámico. Resultado, con la CA ya instalada en el dispositivo:

```
Hostname mismatch, certificate is not valid for 'ringaping.ddns.net'
```

En Chrome es `ERR_CERT_COMMON_NAME_INVALID`. Desde la red local, por IP, el
mismo manager y el mismo certificado validan sin una queja: por eso el fallo
parece «sólo desde fuera» y apunta al router, que no tiene nada que ver.

Se arregla dándole el nombre una vez:

```
python -m tools.make_local_cert ringaping.ddns.net
```

La CA se reutiliza, así que no hay que reinstalar nada en los dispositivos; el
manager sí necesita reinicio, porque el contexto TLS se arma al arrancar.

## La trampa de la renovación

El `names.txt` de `runtime/tls/` existe por esto: los nombres dados a mano se
anotan y vuelven a entrar en cada renovación. Antes, un
`python -m tools.make_local_cert` sin argumentos —lo que dice el README cuando
cambia una IP— regeneraba el certificado **sin** el dominio y reaparecía el
fallo de arriba, a distancia de semanas de su causa. Lo cierra
`tests/test_manager_tls.py`.

## Antes de culpar al router

El reenvío y el DDNS se comprueban en un segundo desde el propio equipo, y
distinguen las dos capas:

```
curl -k  https://ringaping.ddns.net:8750/api/pulse        # ¿llega el puerto?
python -c "import ssl,socket; ctx=ssl.create_default_context(cafile='runtime/tls/ca.crt'); ..."
```

Si el primero da 200, la red está bien y el problema es el certificado o el
esquema. La validación con nombre hay que hacerla con el `ssl` de Python: el
`curl` de Windows usa schannel y tapa cualquier error con «the revocation
status is unknown».
