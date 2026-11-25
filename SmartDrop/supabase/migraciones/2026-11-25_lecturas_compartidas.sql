-- Lecturas compartidas entre viviendas (25/11/2026)
--
-- Cada vivienda sigue siendo una casa distinta, con su propio dueño, pero puede mostrar los sensores de otra.
-- Si id_vivienda_lecturas está vacío, la vivienda usa sus propias lecturas (lo normal).
-- Ejemplo: la casa NIC-0004 muestra lo que mide el ESP32 instalado en NIC-0001.

alter table public.vivienda
    add column if not exists id_vivienda_lecturas integer
    references public.vivienda (id_vivienda) on delete set null;

comment on column public.vivienda.id_vivienda_lecturas is
    'Vivienda cuyos sensores se muestran en esta. Vacío = usa sus propios sensores.';

-- NIC-0004 toma las lecturas del ESP32 de NIC-0001.
update public.vivienda
   set id_vivienda_lecturas = (select id_vivienda from public.vivienda where nic = 'NIC-0001')
 where nic = 'NIC-0004';

-- Que la API de Supabase vea la columna nueva de inmediato.
notify pgrst, 'reload schema';
