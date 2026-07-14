# PII-Cloud

Repositorio independiente para construir, desplegar y operar los componentes
cloud del proyecto PII.

## Independencia del repositorio principal

Este repositorio contiene copias propias de los módulos que necesitan los jobs:

- `Text_Extract/`
- `Entity_Text_Extract/`
- `Entity_Text_Filter/`
- `Table_Extract/`

Las copias corresponden al snapshot `0a3fed48010c03d49bb718870acb87269e90ab39`
del repositorio `PII-LdD`, tomado el 14 de julio de 2026. Desde esta separación
no existe sincronización automática: los cambios realizados aquí o en el repo
principal deben portarse expresamente cuando corresponda.

No hay submódulos, symlinks, imports ni contextos Docker que dependan de una
copia vecina de `Proyecto`.

## Estructura

- `Cloud/`: jobs, infraestructura SQL y utilidades operacionales.
- `Text_Extract/`: copia cloud del extractor de texto.
- `Entity_Text_Extract/`: copia cloud del extractor de entidades.
- `Entity_Text_Filter/`: copia cloud del filtro de entidades.
- `Table_Extract/`: copia cloud del analizador de fuentes tabulares y BBDD.

Todos los comandos de build deben ejecutarse desde la raíz de este repositorio.
Los Dockerfiles conservan el contexto y las rutas `Cloud/...` del proyecto
original para minimizar cambios durante la separación.

## Configuración sensible

Los archivos versionados contienen únicamente valores vacíos o de ejemplo. Las
configuraciones reales se mantienen en archivos `*.local.*`, ignorados por Git.
Las credenciales de base de datos deben inyectarse mediante Secret Manager o
variables locales y nunca deben agregarse a un commit.

`Visor` no forma parte de este repositorio: continúa como herramienta local en
la carpeta hermana `../Visor`.
