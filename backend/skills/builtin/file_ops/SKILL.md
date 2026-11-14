---
name: file-ops
description: "Operaciones de archivos: leer, listar directorios, crear archivos"
triggers:
  - "lee el archivo "
  - "lee archivo "
  - "lista archivos"
  - "lista de archivos"
  - "crea archivo "
  - "crea un archivo "
requires:
  python: []
provides:
  slash_commands:
    - name: "lee archivo"
      description: "Leer contenido de un archivo"
    - name: "lista archivos"
      description: "Listar archivos en un directorio"
    - name: "crea archivo"
      description: "Crear un archivo nuevo con contenido"
---

# File Operations Skill

Permite leer, listar y crear archivos en el sistema de archivos local.

## Uso

- `lee el archivo /ruta/al/archivo.txt`
- `lista archivos en /ruta/directorio`
- `crea archivo nombre.txt con contenido aquí`
