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

## Y aun así la app seguía sin entrar: Android no es el navegador

Con el nombre ya dentro del certificado, la app del móvil siguió cortando:

```
java.security.cert.CertPathValidatorException: Trust anchor for certification
path not found
```

Ése es otro fallo, no el de arriba, y conviene no confundirlos: el primero es
el **nombre** y el segundo la **confianza**. Y la confianza no se arregla
instalando la CA, porque **desde Android 7 (API 24) las apps no usan el almacén
del usuario**, sólo el del sistema. Instalar `ca.crt` en Ajustes arregla Chrome
y no arregla ninguna app: la CA aparece en la lista, parece puesta, y la app
sigue dando exactamente el mismo error. Las salidas serían empaquetar la CA en
el APK con un `network_security_config`, o tener un certificado público.

## La solución: un proxy delante, no más plomería

Lo resuelto el 2026-10-20 es lo segundo, y de paso se lleva por delante toda la
plomería de la CA propia. El servicio `proxy` del `docker-compose.yml` es Caddy:
escucha en el 443, pide el certificado a Let's Encrypt, lo renueva solo y
reenvía a `manager:8750` por la red interna. El manager vuelve a `http` ahí
dentro (`MT5_MANAGER_TLS=off`), y el contexto seguro lo decide el navegador por
lo que ve él, que es `https` con un certificado válido.

Dos cosas que costaron y quedan escritas:

- **El puerto 80 de este equipo no se puede usar**, lo tiene `iphlpsvc` -el IP
  Helper de Windows-, que responde un 200 vacío a cualquier ruta, incluida
  `/.well-known/acme-challenge/...`. La validación leería ese vacío y fallaría.
  No es `netstat` quien lo delata, que sólo dice «svchost»: es
  `Get-CimInstance Win32_Service -Filter "ProcessId=<pid>"`.
- **Por eso el `Caddyfile` lleva `disable_http_challenge`**, que fuerza
  TLS-ALPN-01: el desafío viaja por el 443, el único puerto que se publica. Sin
  esa línea Caddy intentaría primero el 80 y habría que tocar el router o parar
  un servicio de Windows en cada renovación.

Con esto, `https://ringaping.ddns.net` -sin puerto- vale dentro y fuera, para el
navegador y para la app, sin instalar nada en ningún dispositivo. La CA propia
sigue en el repositorio porque es la única opción de una instalación que no
publica dominio, pero ya no es el camino de este equipo.
