import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Eliminar registros DJCS_ duplicados que redirigían el menú "Declaración IVA"
    # a ve.conciliacion.periodo en lugar de ve.declaracion.iva.
    # Fueron quitados del XML en v19.0.2.9.1 pero quedan en el DB hasta que se borre
    # explícitamente (Odoo no elimina automáticamente registros removidos del XML).

    # 1. ir.actions.act_window.view: ligaban DJCS_declaracion_iva_action a vistas
    #    de ve.conciliacion.periodo (modelo incorrecto)
    cr.execute("""
        DELETE FROM ir_act_window_view
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 've_retencion_iva'
              AND name IN (
                  'DJCS_declaracion_iva_action_list_view',
                  'DJCS_declaracion_iva_action_form_view'
              )
        )
    """)
    _logger.info('ve_retencion_iva 19.0.2.9.1: ir_act_window_view DJCS eliminados')

    # 2. ir.ui.view: vistas de ve.conciliacion.periodo con priority=20 que se
    #    sobreponían al form nativo de ve.conciliacion.periodo
    cr.execute("""
        DELETE FROM ir_ui_view
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 've_retencion_iva'
              AND name IN ('DJCS_declaracion_iva_list', 'DJCS_declaracion_iva_form')
        )
    """)
    _logger.info('ve_retencion_iva 19.0.2.9.1: ir_ui_view DJCS eliminados')

    # 3. Limpiar los XMLID huérfanos de ir_model_data
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 've_retencion_iva'
          AND name IN (
              'DJCS_declaracion_iva_action_list_view',
              'DJCS_declaracion_iva_action_form_view',
              'DJCS_declaracion_iva_list',
              'DJCS_declaracion_iva_form'
          )
    """)
    _logger.info('ve_retencion_iva 19.0.2.9.1: ir_model_data DJCS limpiados')
