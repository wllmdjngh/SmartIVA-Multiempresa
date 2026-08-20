import logging

_logger = logging.getLogger(__name__)

# Campos nuevos res.partner.validado_seniat (Selection si/no, en blanco por
# defecto) y ve.conecta.carga.ventas.linea.validado_seniat (Char, raw del
# Excel) — ver models/res_partner.py y models/ve_conecta_carga_ventas.py.
# Pedido explícito 2026-08-05: columna opcional "Validado_SENIAT" en el
# Libro de Ventas que se sincroniza al Cliente al confirmar la carga.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE res_partner ADD COLUMN IF NOT EXISTS validado_seniat varchar
    """)
    cr.execute("""
        ALTER TABLE ve_conecta_carga_ventas_linea
        ADD COLUMN IF NOT EXISTS validado_seniat varchar
    """)
    _logger.info(
        've_retencion_iva 19.0.2.14.5: columnas validado_seniat aseguradas '
        'en res_partner y ve_conecta_carga_ventas_linea')
