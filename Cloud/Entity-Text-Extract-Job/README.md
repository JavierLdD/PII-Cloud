# Cloud Run Job Entity Text Extract

Wrapper cloud que consume `file.chunks_ready`, lee los chunks desde Cloud SQL,
ejecuta detección y filtrado de entidades, persiste los resultados y publica
los artefactos configurados en GCS.

La documentación canónica del contrato, modelos, persistencia y fallos está en
[`docs/jobs/entity-text-extract.md`](../../docs/jobs/entity-text-extract.md).

Todos los comandos de build, deploy y ejecución deben lanzarse desde la raíz
del repositorio usando los scripts de este directorio.
