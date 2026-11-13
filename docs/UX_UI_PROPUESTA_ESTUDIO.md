# Propuesta UX/UI: Estudio

Propuesta exclusivamente visual para Extrude. Mantiene la identidad salvia, azul
pizarra y acento cálido, con una jerarquía más clara y superficies coherentes entre
la bienvenida, el espacio de trabajo, los diálogos y la guía.

## Cambios implementados

| Área | Mejora visual |
| --- | --- |
| Sistema compartido | Superficies claras más luminosas y oscuras más diferenciadas; textos secundarios más contrastados y foco de teclado opaco. |
| Barra superior | Distribución que reserva el ancho de las herramientas y evita que se solapen con las acciones del proyecto. |
| Paneles | Cabeceras diferenciadas, pestaña activa subrayada, etiquetas compactas que caben en el panel y títulos con jerarquía tipográfica. |
| Formularios e inspector | Bordes más definidos, valores numéricos alineados y lectura más cómoda de avisos y consentimientos. |
| Bienvenida y proyectos | Cabecera con degradado discreto, tarjetas redondeadas y superficies diferenciadas para carga, ejemplos y proyectos. |
| Vistas, capas y materiales | Acabado consistente de tarjetas, áreas de edición y estados informativos mediante estilos compartidos. |
| Biblioteca y generación de imágenes | Cabeceras legibles, espaciado uniforme, pasos en tarjetas y pie de acciones separado del contenido desplazable. |
| Menús, avisos y acceso | Radios y elevación coherentes con los diálogos del resto de la aplicación. |
| Guía | Tarjetas de entrada más claras, índice activo destacado, tablas y preguntas frecuentes con mayor separación. |

## Alcance técnico

Solo se modifican `viewer/src/styles.css` y `viewer/src/guide/guide.css`, además de
este documento. No se modifican TypeScript, HTML, Python, API, lógica, datos,
dependencias ni contenido de los mensajes. Se conservan los estados de visibilidad,
las acciones, los atajos y las dimensiones del área del visor.

Los estilos de componentes recientes se aplican desde la hoja compartida con
selectores acotados; `features.css` se conserva tal como estaba en el espacio de
trabajo. Algunas de estas pantallas pertenecen a cambios locales anteriores y la
propuesta se ha revisado sobre ese estado del repositorio.

## Validación

- Compilación de producción con TypeScript y Vite correcta. Vite avisa del tamaño
  del paquete JavaScript principal.
- Formato de las dos hojas de estilo comprobado con Prettier.
- Revisión visual en navegador a 1280 × 720: bienvenida, visor con el ejemplo
  Manzana 2D en claro y oscuro, biblioteca vacía, diálogo de generación y guía.
- Corregidos durante la revisión el solapamiento de herramientas superiores y el
  desbordamiento de la pestaña Materiales.
- No se ejecutaron conversiones ni generaciones con IA: el servidor de conversión
  no estaba disponible en esta vista previa. Los estados con datos de proyectos,
  autenticación y resoluciones móviles requieren una revisión visual adicional.

## Recuperar la propuesta

El stash se identifica por el mensaje `design: propuesta UX/UI Estudio (solo CSS)`.
Localízalo con `git stash list` y usa `git stash apply 'stash@{N}'` para revisarlo
sin eliminar la copia guardada. Contiene únicamente las dos hojas de estilo y este
documento; los cambios locales anteriores se mantienen fuera del stash.
