# Supabase — base de datos principal

| Archivo | Para qué |
|---|---|
| `schema.sql` | Tablas base (rol, usuario, vivienda, consumo, reportes) y Row Level Security. Se ejecuta una vez en Supabase → *SQL Editor* |
| `migraciones/2026-10-22_seguridad.sql` | Reportes privados/comunitarios y RLS en todas las tablas. Se puede repetir sin romper nada |

* El servidor usa la clave **service_role** (secreta, en el `.env`); con RLS activado y sin políticas, las claves
  públicas (`anon`, `authenticated`) no pueden leer nada.
* Las demás tablas (sensor, lectura, tanque, valvula, log_valvula, alerta, notificacion, fuga, prediccion_desabasto)
  ya existen en el proyecto de Supabase. El diagrama ER completo está en
  [`docs/MANUAL_TECNICO.md`](../../docs/MANUAL_TECNICO.md#4-modelo-de-datos-diagrama-er).
* Antes de cambiar una tabla, agrega el SQL en `migraciones/` con la fecha en el nombre.
