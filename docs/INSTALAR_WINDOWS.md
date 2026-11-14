# Instalar Extrude en un ordenador con Windows

Al terminar tendrás un icono **Extrude** en el escritorio y en el menú Inicio. Con doble clic se
abre Extrude en su propia ventana, como cualquier aplicación. Todo funciona en ese ordenador: los
planos y proyectos no salen de él (salvo lo que envíes a la IA, si la usas).

Se hace **una sola vez** y lleva unos 20 minutos, la mayor parte esperando descargas.

## Qué necesitas

- Windows 10 u 11 de 64 bits, con 8 GB de RAM como mínimo (mejor 16 GB).
- Unos 10 GB libres en el disco.
- Conexión a internet durante la instalación. Después solo hace falta para la IA.

## 1. Instalar Docker Desktop

Docker es el programa que hace funcionar Extrude por dentro. Es gratuito para uso personal.

1. Descárgalo de <https://www.docker.com/products/docker-desktop/> (botón *Download for
   Windows*) y ejecuta el instalador con las opciones que trae marcadas.
2. Reinicia el ordenador si te lo pide.
3. Abre **Docker Desktop** desde el menú Inicio. La primera vez te pedirá aceptar sus
   condiciones; puedes saltarte el inicio de sesión (*Skip*).
4. En su configuración (rueda dentada → *General*), deja marcado **Start Docker Desktop when you
   sign in to your computer**. Así Extrude estará listo cada vez que enciendas el ordenador.

Si al abrirlo dice que hace falta actualizar **WSL**, acepta y deja que lo haga. Si avisa de que
la **virtualización** está desactivada, hay que activarla en la BIOS del ordenador (busca en
internet «activar virtualización» y la marca del ordenador).

## 2. Copiar Extrude al ordenador

Pon la carpeta de Extrude en un sitio fijo, por ejemplo `C:\Extrude` (no la dejes en
*Descargas*, por si se limpia esa carpeta).

- **Desde la versión publicada:** en la página del proyecto en GitHub, *Releases* → la versión
  más reciente → *Source code (zip)*. Descomprime el zip en `C:\Extrude`.
- **Con Git** (permite actualizar con un clic): `git clone` del repositorio en `C:\Extrude`.

Si el repositorio es privado, descárgalo desde tu cuenta y pásale la carpeta (por ejemplo, en
un USB).

## 3. Instalar

1. Dentro de la carpeta de Extrude, haz doble clic en **Instalar Extrude.bat**.
2. Si Windows muestra *«Windows protegió su PC»*, pulsa **Más información → Ejecutar de todas
   formas**. Aparece porque el archivo no viene de una tienda de aplicaciones.
3. Se abre una ventana negra que va explicando lo que hace:
   - crea los accesos directos **Extrude** (escritorio y menú Inicio) y **Actualizar Extrude**
     (menú Inicio);
   - te pregunta si quieres poner las **claves de IA**. Si las tienes, contesta `s`, pégalas
     tras el `=` de cada línea en el Bloc de notas, guarda y cierra. Puedes dejarlo para más
     tarde;
   - prepara Extrude. **La primera vez tarda varios minutos**; es normal ver muchas líneas.
4. Al terminar se abre Extrude solo.

## Usarlo

Doble clic en el icono **Extrude**. Si Docker aún está arrancando, aparece una ventanita que lo
indica; en cuanto está listo, se abre Extrude.

- Suelta tus planos **DWG** o **DXF** en la pantalla de inicio.
- El botón **Guía** de Extrude explica todo paso a paso.
- Ábrelo siempre con el icono: así usa siempre el mismo navegador, que es donde se guardan tus
  **vistas** y la **biblioteca de imágenes**.

## La IA (opcional)

Para generar imágenes hace falta una clave de **OpenAI** o de **Google Gemini**; para mejorar los
textos y editar el proyecto con palabras, una de **Claude** (Anthropic). Las claves van en el
archivo `.env` de la carpeta de Extrude: ábrelo con el Bloc de notas, pega las claves, guarda y
vuelve a abrir Extrude. Cada consulta o imagen puede tener coste en la cuenta de la clave.

## Actualizar

Menú Inicio → **Actualizar Extrude**. Si instalaste con Git, descarga la versión nueva y la
prepara. Si instalaste desde un zip, descarga el zip nuevo, sustituye la carpeta **conservando
el archivo `.env`** y ejecuta *Actualizar Extrude*. Tus proyectos y vistas se conservan.

## Tus datos

- **Proyectos convertidos:** se guardan dentro de Docker, en ese ordenador (los 10 más
  recientes).
- **Vistas e imágenes:** se guardan en el navegador de ese ordenador. Para tener una copia o
  llevarlas a otro equipo, usa **Exportar** dentro de Extrude.
- **Claves de IA:** en el archivo `.env`; no se suben a ningún sitio.

## Si algo no va

| Lo que pasa | Qué hacer |
|---|---|
| «No se encuentra Docker Desktop» | Instálalo (paso 1) y vuelve a abrir Extrude |
| «Docker Desktop no ha terminado de arrancar» | Ábrelo desde el menú Inicio, espera a que diga que está en marcha y vuelve a abrir Extrude |
| «Extrude no responde todavía» | La primera vez tarda más: espera un minuto y vuelve a abrirlo |
| El icono no aparece en el escritorio | Vuelve a ejecutar **Instalar Extrude.bat** |
| Un proveedor de IA aparece «no disponible» | Falta su clave en el archivo `.env` |
| Va muy lento | Cierra programas pesados; Docker necesita unos 4 GB de RAM libres |

Para desinstalar: borra la carpeta de Extrude y los accesos directos, y desinstala Docker Desktop
desde *Configuración → Aplicaciones* si ya no lo usas para nada más.
