# Estado previo y alcance de la tarea

Inspección del 13-11-2026. Rama `materialidad`, HEAD `207d114f4cbf53466d3aa1c6e147434858dd7b0c`.
No había cambios staged (`git diff --cached --binary` vacío). Se conservan todos
los cambios unstaged y archivos sin seguimiento de abajo; no se aplicó ni eliminó
ningún stash previo. No se encontró AGENTS.md en el árbol del repositorio.

## Estado previo exacto

```text
 M CHANGELOG.md
 M README.md
 M docs/CONFIG.md
 M docs/EXPORT_FORMATS.md
 M pipeline/src/extrude/build.py
 M pipeline/src/extrude/cli.py
 M pipeline/src/extrude/config.py
 M pipeline/src/extrude/gltf_export.py
 M pipeline/src/extrude/heights.py
 M pipeline/src/extrude/init_config.py
 M pipeline/src/extrude/report.py
 M pipeline/src/extrude/server.py
 M pipeline/tests/test_build.py
 M pipeline/tests/test_foundations.py
 M pipeline/tests/test_init_config.py
 M viewer/guia.html
 M viewer/src/app.ts
 M viewer/src/convert/client.ts
 M viewer/src/convert/report.ts
 M viewer/src/export/png.ts
 M viewer/src/guide/guide.css
 M viewer/src/main.ts
 M viewer/src/scene/dispose.test.ts
 M viewer/src/scene/groundCheck.test.ts
 M viewer/src/scene/groundCheck.ts
 M viewer/src/scene/picking.ts
 M viewer/src/styles.css
 M viewer/src/ui/layersPanel.ts
 M viewer/src/ui/materialsPanel.ts
 M viewer/src/ui/qualitySummary.ts
 M viewer/src/ui/topbar.ts
 M viewer/src/ui/viewsPanel.ts
 M viewer/src/ui/welcome.ts
 M viewer/src/views/storage.ts
 M viewer/src/views/viewManager.ts
?? docs/APOYO_Y_COTAS.md
?? docs/AUDITORIA_EDIFICIOS_FLOTANTES.md
?? docs/PROMPT_CORRECCION_EDIFICIOS_FLOTANTES.md
?? docs/UX_UI_PROPUESTA_ESTUDIO.md
?? docs/VISTAS_E_IMAGENES.md
?? pipeline/src/extrude/build_info.py
?? pipeline/src/extrude/classification.py
?? pipeline/src/extrude/image_gen.py
?? pipeline/src/extrude/layer_heights.py
?? pipeline/tests/test_image_gen.py
?? pipeline/tests/test_layer_heights.py
?? pipeline/tests/test_project_repair.py
?? pipeline/tests/test_support_policy.py
?? viewer/src/features.css
?? viewer/src/library/bitmap.ts
?? viewer/src/library/captures.ts
?? viewer/src/library/generation.ts
?? viewer/src/library/images.test.ts
?? viewer/src/library/images.ts
?? viewer/src/project/heights.test.ts
?? viewer/src/project/heights.ts
?? viewer/src/project/recent.ts
?? viewer/src/project/review.test.ts
?? viewer/src/project/review.ts
?? viewer/src/scene/layerHighlight.ts
?? viewer/src/scene/loupe.test.ts
?? viewer/src/scene/loupe.ts
?? viewer/src/ui/generateDialog.ts
?? viewer/src/ui/imageLibrary.ts
?? viewer/src/ui/projectReview.ts
```

## Stashes previos (OID, orden original)

```text
2184131f0d48cfaddcbd7d3ca0fe2f6b29cc92de
f7f5e7049c9ed1f4cb6d28f507ed731299c10439
093a94fe9908417e7d95e650bdec1a121215f268
```

## Cambios de esta tarea

Archivos nuevos: `.dockerignore`, `deploy/`, `.github/workflows/deploy-check.yml`,
`pipeline/src/extrude/http_server.py`, `pipeline/tests/test_http_server.py`,
`pipeline/tests/test_deploy.py`, `viewer/src/convert/serverDefault.test.ts`.
Cambios sobre archivos existentes: reglas privadas en `.gitignore`, selección de
origen en `viewer/src/convert/client.ts`, importación y uso de BoundedHTTPServer
en `pipeline/src/extrude/server.py`. Ningún cambio UX/UI.

Para aislar la tarea se tomó una instantánea con índice Git temporal, sin tocar el
índice real. El stash final contiene solo el delta de esta tarea; el árbol de trabajo
vuelve exactamente al estado previo. Su base es esa instantánea local, no HEAD:
esto conserva un diff exacto incluso en archivos con modificaciones anteriores.
La funcionalidad se probó sobre el estado local completo, que incluye trabajo
previo todavía no integrado en HEAD.

Para recuperar el delta sobre ese mismo estado local, localizar primero el stash
por su mensaje `Extrude servidor real` y usar su OID verificado:

```sh
git stash show -p --binary OID > extrude-servidor.patch
git apply --check extrude-servidor.patch
git apply extrude-servidor.patch
```

Guardar el patch fuera del repositorio o eliminarlo tras aplicarlo. No aplicar el
stash sobre HEAD limpio esperando que aporte las funcionalidades de otras tareas;
no contiene sus cambios en el delta. Revisar conflictos si el trabajo previo ha
evolucionado; no usar reset/clean para forzar la recuperación.
