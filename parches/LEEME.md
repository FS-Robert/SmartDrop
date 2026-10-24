# Parche de la app (SmartDrop-App)

`seguridad-gotita-app.patch` son dos commits de Robert para `alebaires30/SmartDrop-App`, sobre `master` 72479c6:
la seguridad de la app y los arreglos de Gotita. Se aplica en el repositorio de la app con:

    git switch master && git pull --ff-only
    git -c user.name=Robert -c user.email=robertoantoniolopezramirez1@gmail.com am <ruta>/seguridad-gotita-app.patch
    git push origin master

SHA-256: e531a7eceb37295f0c2fe4023a48fce08157cb8bd3dbe1c48a803963a3cde7a2

Esta rama es solo para llevar el parche; no se fusiona en `main`.
