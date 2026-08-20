import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Forzar res_model del action directamente, sin depender de que el XML lo actualice.
    # Necesario porque versiones anteriores de ve_conciliacion_views.xml sobreescribían
    # este record y podía quedar apuntando a ve.conciliacion.periodo en el DB.
    cr.execute("""
        UPDATE ir_act_window
        SET res_model = 've.declaracion.iva'
        WHERE id = (
            SELECT res_id FROM ir_model_data
            WHERE module = 've_retencion_iva'
              AND name = 'DJCS_declaracion_iva_action'
              AND model = 'ir.actions.act_window'
        )
    """)
    cr.execute("SELECT COUNT(*) FROM ir_act_window WHERE res_model = 've.declaracion.iva' AND id = (SELECT res_id FROM ir_model_data WHERE module = 've_retencion_iva' AND name = 'DJCS_declaracion_iva_action' AND model = 'ir.actions.act_window')")
    updated = cr.fetchone()[0]
    _logger.info('ve_retencion_iva 19.0.2.9.2: DJCS_declaracion_iva_action res_model actualizado=%s', updated)

    # Eliminar ir_act_window_view que ligan DJCS_declaracion_iva_action a vistas
    # del modelo incorrecto (ve.conciliacion.periodo). Se busca por action + model,
    # no por XMLID, para cubrir el caso de que el XMLID ya haya sido limpiado.
    cr.execute("""
        DELETE FROM ir_act_window_view
        WHERE act_window_id = (
            SELECT res_id FROM ir_model_data
            WHERE module = 've_retencion_iva'
              AND name = 'DJCS_declaracion_iva_action'
              AND model = 'ir.actions.act_window'
        )
        AND view_id IN (
            SELECT id FROM ir_ui_view
            WHERE model = 've.conciliacion.periodo'
        )
    """)
    _logger.info('ve_retencion_iva 19.0.2.9.2: ir_act_window_view huérfanos eliminados')

    # Eliminar vistas DJCS de ve.conciliacion.periodo con priority=20 que sobrescribían
    # el form nativo. Se busca por nombre conocido además del XMLID por si acaso.
    cr.execute("""
        DELETE FROM ir_ui_view
        WHERE model = 've.conciliacion.periodo'
          AND name IN (
              've.conciliacion.periodo.declaracion.list',
              've.conciliacion.periodo.declaracion.form'
          )
    """)
    _logger.info('ve_retencion_iva 19.0.2.9.2: vistas DJCS de ve.conciliacion.periodo eliminadas')

    # Limpiar ir_model_data huérfanos por si quedaron
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
    _logger.info('ve_retencion_iva 19.0.2.9.2: ir_model_data DJCS limpiados')
