import logging

_logger = logging.getLogger(__name__)

# Rediseño de A1 (2026-09-09, pedido explícito de la usuaria): el Mapeo
# Manual de Columnas pasa de un Text libre ("Header = campo") a un modelo
# propio ve.conecta.carga.ventas.mapeo.manual (2 menús desplegables --
# columna sin reconocer / campo destino o "Ignorar"). El campo Text
# `mapeo_manual` de v19.0.2.14.206 queda huérfano (no se borra, no molesta,
# ver models/ve_conecta_carga_ventas.py). Asegura la tabla nueva por si el
# module update automático de Odoo.sh no llega a correr.


def migrate(cr, version):
    cr.execute("""
        CREATE TABLE IF NOT EXISTS ve_conecta_carga_ventas_mapeo_manual (
            id serial PRIMARY KEY,
            carga_id integer,
            header_original varchar,
            campo_destino varchar,
            create_uid integer,
            create_date timestamp,
            write_uid integer,
            write_date timestamp
        )
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.207: tabla '
        've_conecta_carga_ventas_mapeo_manual asegurada')
