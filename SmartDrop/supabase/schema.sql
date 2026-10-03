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
    contrasena      VARCHAR(128) NOT NULL,  -- BCrypt $2b$ nuevo; PBKDF2 legacy compatible
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

-- ============================================================
-- Sistema de reportes de usuarios
-- ============================================================
-- Reportes de problemas enviados por usuarios (sin agua, mala
-- calidad, fugas, etc.) con adjuntos y chat usuario↔admin.
CREATE TABLE IF NOT EXISTS public.reporte (
    id_reporte          BIGSERIAL PRIMARY KEY,
    id_usuario          BIGINT NOT NULL REFERENCES public.usuario(id_usuario) ON DELETE CASCADE,
    tipo_problema       VARCHAR(30) NOT NULL,
    descripcion         TEXT NOT NULL,
    ubicacion           TEXT,
    foto                VARCHAR(500),          -- legado: adjunto único
    fecha_reporte       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    estado              VARCHAR(20) NOT NULL DEFAULT 'pendiente',  -- pendiente | en_proceso | resuelto
    prioridad           VARCHAR(10) DEFAULT 'media',               -- baja | media | alta
    id_usuario_atiende  BIGINT REFERENCES public.usuario(id_usuario) ON DELETE SET NULL,
    fecha_atencion      TIMESTAMPTZ,
    respuesta_admin     TEXT
);

-- Adjuntos (imágenes/videos) de un reporte. La URL apunta al
-- archivo servido por Django (MEDIA_URL) o a un bucket si migra.
CREATE TABLE IF NOT EXISTS public.reporte_adjunto (
    id_adjunto    BIGSERIAL PRIMARY KEY,
    id_reporte    BIGINT NOT NULL REFERENCES public.reporte(id_reporte) ON DELETE CASCADE,
    url           TEXT NOT NULL,
    tipo          VARCHAR(10) NOT NULL DEFAULT 'imagen',  -- imagen | video
    nombre        VARCHAR(255),
    fecha_subida  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Chat del reporte: mensajes del usuario y de los administradores.
CREATE TABLE IF NOT EXISTS public.reporte_mensaje (
    id_mensaje    BIGSERIAL PRIMARY KEY,
    id_reporte    BIGINT NOT NULL REFERENCES public.reporte(id_reporte) ON DELETE CASCADE,
    id_usuario    BIGINT NOT NULL REFERENCES public.usuario(id_usuario) ON DELETE CASCADE,
    mensaje       TEXT NOT NULL,
    fecha_envio   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    leido         BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_reporte_usuario ON public.reporte (id_usuario);
CREATE INDEX IF NOT EXISTS idx_reporte_estado ON public.reporte (estado);
CREATE INDEX IF NOT EXISTS idx_reporte_fecha ON public.reporte (fecha_reporte DESC);
CREATE INDEX IF NOT EXISTS idx_adjunto_reporte ON public.reporte_adjunto (id_reporte);
CREATE INDEX IF NOT EXISTS idx_mensaje_reporte ON public.reporte_mensaje (id_reporte, id_mensaje);

-- Habilitar REST API (PostgREST expone tablas en schema public por defecto)
-- Con service_role key, Django puede leer/escribir sin RLS adicional.

