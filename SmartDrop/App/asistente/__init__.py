"""Gotita, la asistente de voz de SmartDrop (web y app, admin y usuario).

Partes:
* `destinos`  — las pantallas a las que Gotita puede llevar a cada persona (admin o usuario).
* `seguridad` — cuidados de lo que entra y sale (sin código y sin contraseñas).
* `contexto`  — los datos del agua que Gotita puede consultar (solo para leer).
* `claude`    — la conversación con Claude y los avisos cortos.
* `avisos`    — avisos de sensores y fugas contados por Gotita.
* `vistas`    — las direcciones que usan la web y la app.

Regla principal: Gotita solo habla, explica y cambia de pantalla. No puede guardar datos, abrir o cerrar
sesión ni mover la válvula; aquí no se usa nada del control de la válvula (lo revisan las pruebas en
`App/tests_asistente.py`).
"""
