import logging

_logger = logging.getLogger(__name__)

# C1 (2026-09-10): Calendario SENIAT real de vencimientos por ultimo digito
# de RIF/quincena/mes, dato GLOBAL (no por compania) -- reemplaza la regla
# fija "fecha_fin + 7 dias" en _compute_vencida (ve_declaracion_iva.py) y
# las 2 comparaciones de puntualidad fiscal del Dashboard
# (ve_dashboard_iva.py). Ver models/ve_calendario_seniat.py +
# wizards/wizard_calendario_seniat.py (wizard OCR con Claude Vision, con
# pantalla de revision editable antes de confirmar).


def migrate(cr, version):
    cr.execute("""
        CREATE TABLE IF NOT EXISTS ve_calendario_seniat (
            id serial PRIMARY KEY,
            anio integer,
            providencia varchar,
            gaceta_oficial varchar,
            fecha_gaceta date,
            archivo bytea,
            archivo_nombre varchar,
            estado varchar,
            create_uid integer,
            create_date timestamp,
            write_uid integer,
            write_date timestamp
        )
    """)
    cr.execute("""
        CREATE TABLE IF NOT EXISTS ve_calendario_seniat_linea (
            id serial PRIMARY KEY,
            calendario_id integer,
            quincena varchar,
            mes integer,
            rif_digito integer,
            dia_limite integer,
            create_uid integer,
            create_date timestamp,
            write_uid integer,
            write_date timestamp
        )
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.214: tablas ve_calendario_seniat + '
        've_calendario_seniat_linea aseguradas')
