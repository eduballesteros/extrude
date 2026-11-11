# Contribuir a Extrude

## Preparar el entorno

Usa Python 3.12 o superior, Node.js 22.12 o superior, uv y just. Desde la raíz:

```sh
just setup
just check
```

El conversor y el visor se arrancan en terminales separadas con `just serve` y `just dev`.
Los modelos de ejemplo se copian automáticamente. Para procesar DWG instala ODA o LibreDWG;
las pruebas sintéticas trabajan principalmente con DXF.

## Preparar un cambio

1. Crea una rama con un nombre que describa el cambio.
2. Mantén las modificaciones centradas en un problema o funcionalidad.
3. Para fallos de geometría, añade un DXF sintético mínimo en una prueba; no incluyas planos privados.
4. Verifica con `just check` y `npm run build --prefix viewer`.
5. Si cambia la conversión o el formato de salida, ejecuta `just convert-samples` y el validador glTF.
6. Actualiza la documentación y la sección «Sin publicar» del changelog.

Las pruebas de servicios usan HTTP local y archivos temporales; las del visor simulan
las descargas para evitar enviar planos a servicios externos. Las herramientas opcionales
pueden producir pruebas omitidas: indica cuáles faltan en la descripción del cambio.

## Commits y revisiones

Separa motor de conversión, servidor, visor y documentación cuando sean cambios independientes.
Escribe mensajes concretos: qué comportamiento corrigen o añaden. Puedes usar prefijos
`feat`, `fix`, `docs`, `test` y `chore` con un ámbito como `pipeline`, `server` o `viewer`.
Conserva los archivos de bloqueo `pipeline/uv.lock` y los `package-lock.json`.

La descripción de una revisión debe indicar el problema, el resultado esperado, cómo se
comprobó y las limitaciones que sigan presentes. Incluye identificadores de entidades y
cotas si son necesarios para entender una corrección del terreno.

## Archivos generados y marca

No incluyas `out/`, entornos virtuales, `node_modules/`, `dist/`, decodificadores copiados
ni modelos locales de prueba. Los GLB sintéticos de `samples/glb/` y los recursos de marca
sí están versionados. Regenera los iconos con `just icons` desde su geometría canónica;
no edites cada PNG o ICO por separado.

## Licencia

La licencia de distribución está pendiente de definición. No añadas cabeceras ni declaraciones
de licencia distintas sin acordarlo con la persona responsable del proyecto.
