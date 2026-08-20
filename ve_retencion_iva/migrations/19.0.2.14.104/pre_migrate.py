import logging

_logger = logging.getLogger(__name__)

# Campos nuevos ve.wh.iva.seniat_rif / seniat_nro_control / seniat_nro_documento
# -- ver models/ve_wh_iva.py. Pedido explicito 2026-08-18: la vista "Ver
# Normalizadas" mostraba solo el lado Odoo del match SENIAT, faltaba el
# RIF/N.Control/N.Doc que trae SENIAT para poder comparar visualmente.


def migrate(cr, version):
    for col in ("seniat_rif", "seniat_nro_control", "seniat_nro_documento"):
        cr.execute(f"""
            ALTER TABLE ve_wh_iva ADD COLUMN IF NOT EXISTS {col} varchar
        """)
    _logger.info(
        've_retencion_iva 19.0.2.14.104: columnas seniat_rif/seniat_nro_control/'
        'seniat_nro_documento aseguradas en ve_wh_iva')
