-- SmartDrop · Cambios de seguridad del 2026-10-22.
-- Ejecutar una vez en Supabase → SQL Editor. Se puede repetir sin romper nada.
--
-- ANTES de ejecutar la parte 2: confirma que el servidor usa la clave service_role (secreta) y no la
-- anon (pública). `python manage.py check` avisa si SUPABASE_KEY es la anon. Con la anon, después de
-- este script el servidor ya no podría leer ninguna tabla.

-- ── 1. Reportes privados y comunitarios ─────────────────────────────────────
-- Antes, el muro comunitario mostraba TODOS los reportes (también los privados de "Reportar un
-- problema", con descripción, dirección y evidencias). Ahora solo los marcados como comunitarios.
ALTER TABLE public.reporte ADD COLUMN IF NOT EXISTS es_comunitario BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_reporte_comunitario ON public.reporte (es_comunitario, fecha_reporte DESC);

-- Todos los reportes existentes quedan privados. Si quieres volver a publicar en el muro los que se
-- crearon desde la web comunitaria con zona, revisa esta lista y luego quita los comentarios del UPDATE:
--   SELECT id_reporte, fecha_reporte, ubicacion FROM public.reporte WHERE ubicacion LIKE '[zona:%';
--   UPDATE public.reporte SET es_comunitario = TRUE WHERE id_reporte IN (...);

-- ── 2. Row Level Security en todas las tablas ───────────────────────────────
-- Sin RLS, cualquiera con la clave anon (pública por diseño) podía leer los hashes de contraseñas,
-- darse el rol de admin o borrar datos llamando directo a la API REST de Supabase. Con RLS activado y
-- sin políticas, las claves anon y authenticated no ven nada; la service_role del servidor no se ve afectada.
DO $$
DECLARE
    tabla record;
BEGIN
    FOR tabla IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tabla.tablename);
    END LOOP;
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;

-- Comprobación: debe devolver 0 filas (ninguna tabla pública sin RLS).
SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND NOT rowsecurity;
