-- ============================================================
-- SmartDrop — Esquema mínimo para Supabase
-- Ejecutar en: Supabase Dashboard → SQL Editor → Run
-- ============================================================

-- Tabla de roles
CREATE TABLE IF NOT EXISTS public.rol (
    id_rol       BIGSERIAL PRIMARY KEY,
    nombre_rol   VARCHAR(60) NOT NULL UNIQUE,
    descripcion  VARCHAR(200) DEFAULT ''
);

-- Tabla de usuarios (fuente de verdad para auth web + móvil)
CREATE TABLE IF NOT EXISTS public.usuario (
    id_usuario      BIGSERIAL PRIMARY KEY,
    nombre          VARCHAR(255) NOT NULL,
    apellido        VARCHAR(255) NOT NULL,
    correo          VARCHAR(254) NOT NULL UNIQUE,
    contrasena      VARCHAR(128) NOT NULL,  -- hash Django PBKDF2
    id_rol          BIGINT REFERENCES public.rol(id_rol),
    estado_usuario  BOOLEAN DEFAULT TRUE,
    fecha_registro  TIMESTAMPTZ DEFAULT NOW()
);

-- Roles iniciales
INSERT INTO public.rol (nombre_rol, descripcion)
VALUES
    ('user',  'Usuario estándar'),
    ('admin', 'Administrador')
ON CONFLICT (nombre_rol) DO NOTHING;

-- Vivienda y consumos consultados por la vista del usuario.
CREATE TABLE IF NOT EXISTS public.vivienda (
    id_vivienda             BIGSERIAL PRIMARY KEY,
    nic                     VARCHAR(50) NOT NULL UNIQUE,
    direccion               TEXT NOT NULL,
    id_usuario_propietario  BIGINT REFERENCES public.usuario(id_usuario) ON DELETE SET NULL,
    codigo_medidor          VARCHAR(50) NOT NULL,
    tipo_establecimiento    VARCHAR(30) NOT NULL,
    nombre_completo_titular VARCHAR(150) NOT NULL,
    fecha_vinculacion       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS public.consumo (
    id_consumo       BIGSERIAL PRIMARY KEY,
    id_vivienda      BIGINT NOT NULL REFERENCES public.vivienda(id_vivienda) ON DELETE RESTRICT,
    fecha            TIMESTAMPTZ NOT NULL,
    consumo_total    NUMERIC,
    consumo_promedio NUMERIC,
    consumo_maximo   NUMERIC,
    consumo_minimo   NUMERIC,
    periodo          VARCHAR(20) NOT NULL,
    estado_pago      VARCHAR(15) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vivienda_propietario
    ON public.vivienda (id_usuario_propietario);
CREATE UNIQUE INDEX IF NOT EXISTS idx_consumo_vivienda_fecha_periodo
    ON public.consumo (id_vivienda, fecha, periodo);

-- Retroalimentación de consumo (ya usada por la vista web)
CREATE TABLE IF NOT EXISTS public.retroalimentacion_consumo (
    id                      BIGSERIAL PRIMARY KEY,
    mensaje_generado        TEXT,
    diferencia_consumo      NUMERIC,
    consumo_total           NUMERIC,
    consumo_promedio        NUMERIC,
    fecha_registro          TIMESTAMPTZ DEFAULT NOW()
);

-- Índices útiles
CREATE INDEX IF NOT EXISTS idx_usuario_correo ON public.usuario (correo);
CREATE INDEX IF NOT EXISTS idx_retro_fecha ON public.retroalimentacion_consumo (fecha_registro DESC);

-- Habilitar REST API (PostgREST expone tablas en schema public por defecto)
-- Con service_role key, Django puede leer/escribir sin RLS adicional.

