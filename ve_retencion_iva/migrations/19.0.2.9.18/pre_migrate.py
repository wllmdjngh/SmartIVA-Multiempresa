import logging

_logger = logging.getLogger(__name__)

# REQ-09: Prorrateo créditos fiscales (Art. 34 LIVA)
# Agrega columnas aplica_prorrata y porcentaje_prorrata a ve_declaracion_iva.


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE ve_declaracion_iva
        ADD COLUMN IF NOT EXISTS aplica_prorrata boolean DEFAULT false;
    """)
    cr.execute("""
        ALTER TABLE ve_declaracion_iva
        ADD COLUMN IF NOT EXISTS porcentaje_prorrata double precision DEFAULT 100.0;
    """)
    cr.execute("""
        UPDATE ve_declaracion_iva
        SET porcentaje_prorrata = 100.0
        WHERE porcentaje_prorrata IS NULL OR porcentaje_prorrata = 0;
    """)
    _logger.info('ve_retencion_iva 19.0.2.9.18: columnas aplica_prorrata y porcentaje_prorrata agregadas a ve_declaracion_iva')
