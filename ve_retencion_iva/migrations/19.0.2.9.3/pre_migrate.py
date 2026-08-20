import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Eliminar el action DJCS_declaracion_iva_action del DB para que el nuevo
    # ve_declaracion_iva_action se cree limpio desde ve_declaracion_iva_views.xml.
    # La raíz del problema: el XMLID DJCS_ tenía res_model=ve.conciliacion.periodo
    # en el DB y las migraciones anteriores no lograron actualizarlo limpiamente.

    # 1. Limpiar menu que apuntaba al action viejo (se re-crea desde menu_views.xml)
    cr.execute("""
        DELETE FROM ir_ui_menu
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 've_retencion_iva' AND name = 'DJCS_menu_declaracion_iva'
        )
    """)
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 've_retencion_iva' AND name = 'DJCS_menu_declaracion_iva'
    """)
    _logger.info('ve_retencion_iva 19.0.2.9.3: menu DJCS_declaracion_iva eliminado')

    # 2. Limpiar ir_act_window_view que apuntaban al action viejo
    cr.execute("""
        DELETE FROM ir_act_window_view
        WHERE act_window_id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 've_retencion_iva' AND name = 'DJCS_declaracion_iva_action'
        )
    """)
    _logger.info('ve_retencion_iva 19.0.2.9.3: ir_act_window_view del action viejo eliminados')

    # 3. Eliminar el action viejo del DB
    cr.execute("""
        DELETE FROM ir_act_window
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 've_retencion_iva' AND name = 'DJCS_declaracion_iva_action'
        )
    """)
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 've_retencion_iva' AND name = 'DJCS_declaracion_iva_action'
    """)
    _logger.info('ve_retencion_iva 19.0.2.9.3: ir_act_window DJCS_declaracion_iva_action eliminado')

    # 4. También limpiar vistas DJCS de ve.conciliacion.periodo que pudieran quedar
    cr.execute("""
        DELETE FROM ir_ui_view
        WHERE model = 've.conciliacion.periodo'
          AND name IN (
              've.conciliacion.periodo.declaracion.list',
              've.conciliacion.periodo.declaracion.form'
          )
    """)
    _logger.info('ve_retencion_iva 19.0.2.9.3: vistas DJCS de ve.conciliacion.periodo eliminadas')
