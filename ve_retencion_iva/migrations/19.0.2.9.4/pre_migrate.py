import logging

_logger = logging.getLogger(__name__)

# Columnas de ve_conciliacion_periodo eliminadas de la definición Python.
# Las que venían de edits anteriores (sesión 2026-06-25) más las de esta sesión.
_COLS_CONCILIACION = [
    # Eliminados en sesión anterior (edits 1-3)
    'estado_declaracion',
    'declarado_por_id', 'declarado_por_rpa',
    'fecha_declaracion', 'nro_declaracion',
    'planilla_declaracion', 'planilla_declaracion_nombre',
    'estado_prov', 'declarado_prov_por_id', 'fecha_declaracion_prov',
    'nro_declaracion_prov', 'planilla_declaracion_prov', 'planilla_declaracion_prov_nombre',
    'count_wh_iva_prov', 'total_retenido_prov', 'campo_33_arrastre',
    # Eliminados en esta sesión (edits 4-5): campos reporte SENIAT
    'campo_40_base',
    'campo_443_base', 'campo_443_debito',
    'campo_42_base', 'campo_42_debito',
    'campo_442_base', 'campo_442_debito',
    'campo_33_base', 'campo_33_credito',
    'campo_66', 'campo_89', 'campo_90', 'saldo_nuevo_campo_33',
    'campo_49', 'campo_71', 'campo_20', 'campo_39',
]


def migrate(cr, version):
    for col in _COLS_CONCILIACION:
        cr.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 've_conciliacion_periodo' AND column_name = %s
        """, (col,))
        if cr.fetchone():
            cr.execute(
                f'ALTER TABLE ve_conciliacion_periodo DROP COLUMN IF EXISTS "{col}"'
            )
            _logger.info('ve_retencion_iva 19.0.2.9.4: columna %s eliminada de ve_conciliacion_periodo', col)

    # El reporte SENIAT cambia de modelo ve.conciliacion.periodo → ve.declaracion.iva.
    # Eliminar el registro de ir_act_report_xml del DB para que se re-cree con el nuevo modelo.
    cr.execute("""
        DELETE FROM ir_act_report_xml
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 've_retencion_iva' AND name = 'action_report_reporte_seniat'
        )
    """)
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 've_retencion_iva' AND name = 'action_report_reporte_seniat'
    """)
    _logger.info('ve_retencion_iva 19.0.2.9.4: action_report_reporte_seniat eliminado para re-crear con modelo ve.declaracion.iva')
