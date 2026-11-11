/* Importar un portafolio: primitiva compartida por los tres ámbitos.
 *
 * La importación es el reflejo de la exportación y hereda su transporte: con
 * `export_mode=folder` el manager abre su selector nativo y recibe una ruta;
 * con `download` el navegador manda el ZIP. Lo que no puede divergir entre
 * pantallas es cómo se lee ese ZIP y cómo se resume el resultado, así que vive
 * aquí. El botón y su recarga siguen siendo de cada pantalla, como el de
 * exportar.
 */

/* Abre el selector de archivos y devuelve {name, base64}, o null si se cancela. */
function pickPortfolioArchive() {
  return new Promise(resolve => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.zip,application/zip';
    input.style.display = 'none';
    document.body.appendChild(input);
    let settled = false;
    const finish = value => {
      if (settled) return;
      settled = true;
      input.remove();
      resolve(value);
    };
    input.addEventListener('change', () => {
      const file = input.files && input.files[0];
      if (!file) return finish(null);
      const reader = new FileReader();
      reader.onerror = () => finish(null);
      reader.onload = () => {
        // `readAsDataURL` da «data:...;base64,XXXX»: al servidor solo le sirve la carga.
        const text = String(reader.result || '');
        finish({name: file.name, base64: text.slice(text.indexOf(',') + 1)});
      };
      reader.readAsDataURL(file);
    });
    // Cancelar el diálogo no dispara ningún evento en todos los navegadores: el
    // foco vuelve a la ventana y ahí se da por cancelado si no llegó un fichero.
    window.addEventListener('focus', () => setTimeout(() => finish(null), 400), {once: true});
    input.click();
  });
}

/* Resume lo que devolvió el servidor, nombrando lo que no pudo reconstruir. */
function describePortfolioImport(data) {
  const parts = [`Portafolio #${data.portfolio_id} importado con ${data.strategies} estrategia(s)`];
  if ((data.variants || []).length > 1) parts.push(`${data.variants.length} variantes`);
  const pending = [];
  if ((data.unresolved || []).length) pending.push(`${data.unresolved.length} set(s) ya no son candidatos`);
  if ((data.ambiguous || []).length) pending.push(`${data.ambiguous.length} con nombre repetido`);
  if ((data.missing_set_files || []).length) pending.push(`${data.missing_set_files.length} sin fichero .set en la carpeta`);
  return parts.join(' · ') + (pending.length ? `. Fuera: ${pending.join('; ')}.` : '.');
}

/* Pide el origen segun el modo de exportacion. Devuelve null si se cancela.
 *
 * Elegir el origen y ejecutar la importacion son dos pasos a proposito: la
 * pantalla de carga tiene que aparecer DESPUES del selector. Taparlo con un
 * velo mientras el usuario busca la carpeta seria un estorbo, y el selector
 * nativo corre en la maquina del manager, asi que puede tardar lo que quiera.
 *
 * `label` es para el texto de esa pantalla; el resto del objeto es el cuerpo
 * que espera el endpoint.
 */
async function pickPortfolioImportSource(scope, exportMode, post) {
  if (exportMode === 'download') {
    const archive = await pickPortfolioArchive();
    if (!archive) return null;
    return {label: archive.name, filename: archive.name, archive: archive.base64};
  }
  const selection = await post('choose-import-folder', {scope});
  if (selection.cancelled || !selection.folder) return null;
  return {label: selection.folder, folder: selection.folder};
}

/* Texto de la pantalla de carga: reconstruir no es instantaneo. */
function portfolioImportProgress(label) {
  return {
    title: 'Importando portafolio',
    detail: `Leyendo ${label} y recalculando el portafolio desde los informes de cada estrategia…`,
  };
}
