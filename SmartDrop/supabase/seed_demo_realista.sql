-- Semilla de demostracion para la BD existente de SmartDrop (Supabase/PostgreSQL).
-- Solo inserta filas en tablas que ya existen. No crea tablas, extensiones,
-- vistas, funciones ni sensores; reutiliza los sensores existentes.
-- No borra ni actualiza datos existentes.
--
-- Volumen aproximado si las viviendas demo no tienen datos previos:
-- 25 usuarios/viviendas, ~432 mil lecturas, 9,125 consumos diarios,
-- 36 mil mediciones de tanque, 500 reportes, 400 alertas y notificaciones.
-- Fisica: tuberia nominal 1/2 pulgada (12.7 mm); tanque cilindrico
-- de 30 cm de alto x 25 cm de diametro (14.73 L aproximadamente).
-- Cuentas: demo01@smartdrop.test a demo25@smartdrop.test; clave: 1234.

BEGIN;

DO $$
DECLARE
    v_names text[] := ARRAY[
        'María Fernanda', 'José Luis', 'Ana Sofía', 'Carlos Eduardo', 'Karla Patricia',
        'Óscar Mauricio', 'Marta Elena', 'Luis Fernando', 'Gabriela Beatriz', 'Jorge Alberto',
        'Daniela Alejandra', 'Miguel Ángel', 'Claudia Verónica', 'Ricardo Antonio', 'Paola Andrea',
        'Francisco Javier', 'Sofía Guadalupe', 'Kevin Alexander', 'Lorena Isabel', 'Roberto Carlos',
        'Andrés Felipe', 'Beatriz del Carmen', 'Eduardo José', 'María José', 'Salvador Ernesto'
    ];
    v_surnames text[] := ARRAY[
        'Hernández López', 'Martínez García', 'Sorto Guardado', 'Molina Flores', 'Ramírez Cruz',
        'Alvarado Reyes', 'Pérez Chicas', 'Gómez Méndez', 'López Vásquez', 'Castro Aguilar',
        'Sánchez Rivas', 'Rivera Orellana', 'Morales Quintanilla', 'Gutiérrez Pineda', 'Díaz Amaya',
        'Flores Menjívar', 'Mejía Campos', 'Ramos Portillo', 'Vásquez Bonilla', 'Romero Henríquez',
        'Cáceres Escobar', 'Quintanilla Arévalo', 'Reyes Villalta', 'Guardado Marroquín', 'Chávez Renderos'
    ];
    v_zones text[] := ARRAY[
        'Colonia Escalón', 'San Benito', 'San Jacinto', 'Centro Histórico', 'Mejicanos',
        'Soyapango', 'Santa Tecla', 'Antiguo Cuscatlán', 'San Miguelito', 'Ciudad Delgado'
    ];
    v_password_hash text := '$2b$12$sE2kfTca21rTRqyX6LNFHeKWF8C189wbCJBgIk1t8qnrpjIHE.F0C';
    v_now timestamptz := now();
    v_role_user integer;
    v_role_admin integer;
    v_admin_user integer;
    v_flow_sensor integer;
    v_pressure_sensor integer;
    v_level_sensor integer;
    v_tds_sensor integer;
    v_user integer;
    v_home integer;
    v_tank integer;
    v_alert integer;
    v_sequence text;
    v_table text;
    v_column text;
    v_sequence_last bigint;
    v_max_id bigint;
    v_email text;
    v_zone text;
    v_status text;
    v_report_time timestamptz;
    v_i integer;
    v_j integer;
BEGIN
    -- Alinea secuencias existentes con los IDs ya guardados en sus tablas.
    -- No crea ni cambia sensores; evita que un INSERT reciba un ID duplicado.
    FOR v_table, v_column IN
        SELECT x.tabla, x.columna
          FROM (VALUES
              ('rol', 'id_rol'), ('usuario', 'id_usuario'), ('vivienda', 'id_vivienda'),
              ('tanque', 'id_tanque'), ('lectura', 'id_lectura'), ('consumo', 'id_consumo'),
              ('pago', 'id_pago'), ('retroalimentacion_consumo', 'id_retroalimentacion'),
              ('historial_nivel_tanque', 'id_historial'), ('reporte', 'id_reporte'),
              ('alerta', 'id_alerta'), ('notificacion', 'id_notificacion')
          ) AS x(tabla, columna)
    LOOP
        v_sequence := pg_get_serial_sequence(format('public.%I', v_table), v_column);
        IF v_sequence IS NOT NULL THEN
            EXECUTE format(
                'SELECT COALESCE(MAX(%I), 0) FROM public.%I', v_column, v_table
            ) INTO v_max_id;
            EXECUTE format('SELECT last_value FROM %s', v_sequence) INTO v_sequence_last;
            PERFORM setval(v_sequence::regclass,
                           GREATEST(v_max_id, v_sequence_last, 1), true);
        END IF;
    END LOOP;

    -- Busca sensores ya registrados. Si no existe una categoria, se omite esa lectura.
    SELECT id_sensor INTO v_flow_sensor
      FROM public.sensor
     WHERE lower(tipo_sensor) LIKE '%flujo%'
        OR lower(tipo_sensor) LIKE '%flow%'
        OR lower(tipo_sensor) LIKE '%caudal%'
     ORDER BY id_sensor LIMIT 1;
    SELECT id_sensor INTO v_pressure_sensor
      FROM public.sensor
     WHERE lower(tipo_sensor) LIKE '%presi%'
        OR lower(tipo_sensor) LIKE '%pressure%'
     ORDER BY id_sensor LIMIT 1;
    SELECT id_sensor INTO v_level_sensor
      FROM public.sensor
     WHERE lower(tipo_sensor) LIKE '%nivel%'
        OR lower(tipo_sensor) LIKE '%level%'
     ORDER BY id_sensor LIMIT 1;
    SELECT id_sensor INTO v_tds_sensor
      FROM public.sensor
     WHERE lower(tipo_sensor) LIKE '%tds%'
        OR lower(tipo_sensor) LIKE '%solidos disueltos%'
     ORDER BY id_sensor LIMIT 1;

    IF v_flow_sensor IS NULL AND v_pressure_sensor IS NULL
       AND v_level_sensor IS NULL AND v_tds_sensor IS NULL THEN
        RAISE EXCEPTION 'No hay sensores existentes que se puedan reutilizar. No se creó ninguno.';
    END IF;

    INSERT INTO public.rol (nombre_rol, descripcion)
    VALUES ('user', 'Usuario estándar')
    ON CONFLICT (nombre_rol) DO NOTHING;
    SELECT id_rol INTO v_role_user FROM public.rol WHERE nombre_rol = 'user' LIMIT 1;
    SELECT id_rol INTO v_role_admin FROM public.rol WHERE nombre_rol = 'admin' LIMIT 1;
    IF v_role_admin IS NOT NULL THEN
        SELECT id_usuario INTO v_admin_user FROM public.usuario
         WHERE id_rol = v_role_admin ORDER BY id_usuario LIMIT 1;
    END IF;
    IF v_role_user IS NULL THEN
        RAISE EXCEPTION 'No se encontró ni se pudo insertar el rol user.';
    END IF;

    FOR v_i IN 1..25 LOOP
        v_email := 'demo' || lpad(v_i::text, 2, '0') || '@smartdrop.test';
        v_zone := v_zones[1 + ((v_i - 1) % array_length(v_zones, 1))];

        SELECT id_usuario INTO v_user FROM public.usuario WHERE correo = v_email LIMIT 1;
        IF v_user IS NULL THEN
            INSERT INTO public.usuario
                (nombre, apellido, correo, contrasena, estado_usuario, id_rol)
            VALUES (v_names[v_i], v_surnames[v_i], v_email, v_password_hash, true, v_role_user)
            RETURNING id_usuario INTO v_user;
        END IF;

        SELECT id_vivienda INTO v_home FROM public.vivienda
         WHERE nic = 'SD-DEMO-' || lpad(v_i::text, 4, '0') LIMIT 1;
        IF v_home IS NULL THEN
            INSERT INTO public.vivienda
                (nic, direccion, zona, codigo_medidor, id_usuario_propietario,
                 fecha_vinculacion, tipo_establecimiento, telefono_titular,
                 nombre_completo_titular)
            VALUES ('SD-DEMO-' || lpad(v_i::text, 4, '0'),
                    'Pasaje ' || (1 + ((v_i - 1) % 18)) || ', casa ' || (100 + v_i),
                    v_zone || ', El Salvador', 'MED-SD-' || lpad(v_i::text, 5, '0'),
                    v_user, v_now - ((v_i % 180) || ' days')::interval, 'residencial',
                    '+503 7000-' || lpad((1000 + v_i)::text, 4, '0'),
                    v_names[v_i] || ' ' || v_surnames[v_i])
            RETURNING id_vivienda INTO v_home;
        END IF;

        SELECT id_tanque INTO v_tank FROM public.tanque
         WHERE nombre = 'SD-DEMO Tanque ' || lpad(v_i::text, 2, '0') LIMIT 1;
        IF v_tank IS NULL THEN
            INSERT INTO public.tanque
                (nombre, forma_geometrica, altura_total, diametro,
                 capacidad_maxima_litros, nivel_critico_cm, nivel_minimo_cm,
                 id_sensor_nivel, estado)
            VALUES ('SD-DEMO Tanque ' || lpad(v_i::text, 2, '0'), 'cilindrico', 30, 25,
                    round((pi() * (25.0/2)^2 * 30 / 1000)::numeric, 2),
                    7, 3, v_level_sensor, 'normal')
            RETURNING id_tanque INTO v_tank;
        END IF;

        -- Lecturas cada 10 minutos durante 30 dias. Un flujo residencial suele estar
        -- inactivo y muestra caudal cuando se usa; las anomalías son infrecuentes.
        IF NOT EXISTS (SELECT 1 FROM public.lectura WHERE id_vivienda = v_home) THEN
            INSERT INTO public.lectura (id_sensor, id_vivienda, valor, fecha_registro)
            SELECT s.id_sensor, v_home, s.valor, ts.momento
              FROM generate_series(v_now - interval '30 days', v_now, interval '10 minutes') ts(momento)
              CROSS JOIN LATERAL (VALUES
                (v_flow_sensor,
                 CASE WHEN random() < 0.01 THEN 8.5 + random() * 1.2
                      WHEN random() < 0.16 THEN 0.5 + random() * 4.5
                      ELSE random() * 0.08 END),
                (v_pressure_sensor,
                 CASE WHEN random() < 0.012 THEN 0.55 + random() * 0.3
                      ELSE 1.5 + random() * 2.3 END),
                (v_level_sensor,
                 CASE WHEN random() < 0.015 THEN 3 + random() * 3
                      ELSE 8 + random() * 21 END),
                (v_tds_sensor,
                 CASE WHEN random() < 0.008 THEN 500 + random() * 180
                      ELSE 80 + random() * 270 END)
              ) AS s(id_sensor, valor)
             WHERE s.id_sensor IS NOT NULL;
        END IF;

        -- Nivel del tanque en un cilindro de 30 cm x 25 cm; capacidad ~14.73 litros.
        IF NOT EXISTS (SELECT 1 FROM public.historial_nivel_tanque WHERE id_tanque = v_tank) THEN
            INSERT INTO public.historial_nivel_tanque
                (id_tanque, nivel_cm, nivel_litros, porcentaje_llenado, fecha_hora, es_valido)
            SELECT v_tank, round(n.nivel_cm::numeric, 2),
                   round((pi() * (25.0/2)^2 * n.nivel_cm / 1000)::numeric, 2),
                   round((n.nivel_cm / 30 * 100)::numeric, 2), ts.momento, true
              FROM generate_series(v_now - interval '30 days', v_now, interval '30 minutes') ts(momento)
              CROSS JOIN LATERAL (SELECT CASE WHEN random() < 0.025 THEN 3 + random() * 4
                                               ELSE 9 + random() * 20 END AS nivel_cm) n;
        END IF;

        -- Consumo residencial diario. El tanque pequeño puede rellenarse varias veces al dia.
        IF NOT EXISTS (SELECT 1 FROM public.consumo WHERE id_vivienda = v_home) THEN
            INSERT INTO public.consumo
                (id_vivienda, fecha, consumo_total, consumo_promedio, consumo_maximo,
                 consumo_minimo, periodo, estado_pago)
            SELECT v_home, d.dia::date + interval '12 hours', round(d.litros::numeric, 2),
                   round((d.litros / 24)::numeric, 2),
                   round((d.litros * (0.09 + random() * 0.06))::numeric, 2),
                   round((d.litros * (0.005 + random() * 0.015))::numeric, 2),
                   'diario', CASE WHEN random() < 0.88 THEN 'pagado' ELSE 'pendiente' END
              FROM generate_series(current_date - 364, current_date, interval '1 day') d(dia)
              CROSS JOIN LATERAL (SELECT (380 + random() * 380)
                   * CASE WHEN extract(isodow FROM d.dia) IN (6, 7) THEN 1.08 ELSE 1 END
                   * CASE WHEN extract(month FROM d.dia) IN (3, 4, 5) THEN 1.05 ELSE 1 END
                   AS litros) x;
        END IF;

        IF NOT EXISTS (SELECT 1 FROM public.pago
                        WHERE id_vivienda = v_home AND periodo_correspondiente LIKE 'SD-DEMO-%') THEN
            INSERT INTO public.pago
                (id_vivienda, monto_pagado, fecha_pago, metodo_pago, periodo_correspondiente)
            SELECT v_home, round((12 + random() * 28)::numeric, 2),
                   (date_trunc('month', current_date - make_interval(months => m.mes))
                      + ((5 + floor(random() * 20)::integer) || ' days')::interval)::timestamptz,
                   (ARRAY['transferencia', 'efectivo', 'tarjeta'])[1 + floor(random() * 3)::integer],
                   'SD-DEMO-' || to_char(current_date - make_interval(months => m.mes), 'YYYY-MM')
              FROM generate_series(0, 11) m(mes);
        END IF;

        IF NOT EXISTS (SELECT 1 FROM public.retroalimentacion_consumo
                        WHERE id_vivienda = v_home AND tipo_mensaje = 'SD-DEMO') THEN
            INSERT INTO public.retroalimentacion_consumo
                (id_vivienda, tipo_mensaje, mensaje_generado, diferencia_consumo, fecha_registro)
            SELECT v_home, 'SD-DEMO',
                   CASE WHEN n % 3 = 0 THEN 'El consumo semanal se mantiene en el rango habitual.'
                        WHEN n % 3 = 1 THEN 'El consumo subio moderadamente; revisa grifos y sanitarios.'
                        ELSE 'El consumo bajo respecto a la semana anterior. Buen ahorro de agua.' END,
                   round((random() * 30 - 10)::numeric, 2), v_now - (n || ' days')::interval
              FROM generate_series(1, 12) AS series(n);
        END IF;

        IF NOT EXISTS (SELECT 1 FROM public.reporte
                        WHERE id_usuario = v_user AND descripcion LIKE 'SD-DEMO:%') THEN
            FOR v_j IN 1..20 LOOP
                v_report_time := v_now - (random() * 120 || ' days')::interval;
                v_status := CASE WHEN random() < 0.12 THEN 'pendiente'
                                 WHEN random() < 0.23 THEN 'en_proceso'
                                 ELSE 'resuelto' END;
                INSERT INTO public.reporte
                    (id_usuario, tipo_problema, descripcion, ubicacion, fecha_reporte,
                     estado, prioridad, id_usuario_atiende, fecha_atencion, respuesta_admin)
                VALUES (v_user,
                    (ARRAY['sin_agua','mala_calidad','baja_presion','fuga','tanque','facturacion','otro'])
                        [1 + floor(random() * 7)::integer],
                    'SD-DEMO: ' || (ARRAY[
                        'El servicio se interrumpio durante varias horas y regreso por la tarde.',
                        'El agua presento turbidez y olor inusual al abrir el grifo.',
                        'La presion baja en horas de mayor demanda, especialmente por la mañana.',
                        'Se observa una fuga pequeña cerca del medidor.',
                        'La lectura del nivel del tanque no coincide con la inspeccion visual.',
                        'Solicito revisar el monto del ultimo recibo.',
                        'El suministro llega de forma intermitente durante el dia.'
                    ])[1 + floor(random() * 7)::integer],
                    v_zone || ', El Salvador', v_report_time, v_status,
                    CASE WHEN random() < 0.14 THEN 'alta'
                         WHEN random() < 0.75 THEN 'media' ELSE 'baja' END,
                    CASE WHEN v_status <> 'pendiente' THEN v_admin_user ELSE NULL END,
                    CASE WHEN v_status <> 'pendiente'
                         THEN LEAST(v_now, v_report_time + (random() * 5 || ' days')::interval)
                         ELSE NULL END,
                    CASE WHEN v_status = 'pendiente' THEN NULL
                         WHEN v_status = 'resuelto' THEN 'SD-DEMO: Se reviso el sector y se normalizo el servicio.'
                         ELSE 'SD-DEMO: Caso asignado para inspeccion del sector.' END);
            END LOOP;
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM public.notificacion n JOIN public.alerta a USING (id_alerta)
             WHERE n.id_usario_destino = v_user AND a.mensaje LIKE 'SD-DEMO:%'
        ) THEN
            FOR v_j IN 1..16 LOOP
                INSERT INTO public.alerta
                    (tipo_alerta, prioridad, mensaje, estado_confirmacion, fecha_creacion, datos_adicionales)
                VALUES ((ARRAY['consumo_elevado','presion_baja','nivel_tanque_bajo','calidad_agua','fuga_detectada'])
                            [1 + floor(random() * 5)::integer],
                        CASE WHEN random() < 0.08 THEN 'alta'
                             WHEN random() < 0.38 THEN 'media' ELSE 'baja' END,
                        'SD-DEMO: ' || (ARRAY[
                            'Consumo diario por encima del promedio reciente.',
                            'Presion de entrada por debajo del rango habitual.',
                            'El tanque se acerca al nivel minimo recomendado.',
                            'La lectura TDS supero temporalmente el umbral de seguimiento.',
                            'Patron de flujo continuo que conviene revisar.'
                        ])[1 + floor(random() * 5)::integer],
                        CASE WHEN random() < 0.68 THEN 'pendiente' ELSE 'confirmada' END,
                        v_now - (random() * 60 || ' days')::interval,
                        jsonb_build_object('demo', true, 'origen', 'semilla_realista'))
                RETURNING id_alerta INTO v_alert;

                INSERT INTO public.notificacion
                    (id_alerta, id_usario_destino, canal_envio, estado_visualizacion,
                     fecha_envio, fecha_leida)
                VALUES (v_alert, v_user, 'dashboard',
                        CASE WHEN random() < 0.72 THEN 'no_leida' ELSE 'leida' END,
                        v_now - (random() * 60 || ' days')::interval,
                        CASE WHEN random() < 0.72 THEN NULL
                             ELSE v_now - (random() * 30 || ' days')::interval END);
            END LOOP;
        END IF;
    END LOOP;
END $$;

COMMIT;

-- Resumen de filas demo insertadas.
SELECT
    (SELECT count(*) FROM public.vivienda WHERE nic LIKE 'SD-DEMO-%') AS viviendas_demo,
    (SELECT count(*) FROM public.lectura l JOIN public.vivienda v USING (id_vivienda)
      WHERE v.nic LIKE 'SD-DEMO-%') AS lecturas,
    (SELECT count(*) FROM public.consumo c JOIN public.vivienda v USING (id_vivienda)
      WHERE v.nic LIKE 'SD-DEMO-%') AS consumos_diarios,
    (SELECT count(*) FROM public.reporte WHERE descripcion LIKE 'SD-DEMO:%') AS reportes_demo,
    (SELECT count(*) FROM public.alerta WHERE mensaje LIKE 'SD-DEMO:%') AS alertas_demo;
