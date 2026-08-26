{
    'name': 'Venezuela - Retenciones IVA',
    'version': '19.0.2.14.155',
    'category': 'Accounting/Localizations',
    'summary': 'Retenciones IVA Venezuela – DJCS',
    'description': '''
Gestión completa de retenciones IVA de clientes (Agentes SPE) para empresas venezolanas.
Marco legal: PA SNAT/2025/000054

FLUJO: Factura → Retención Esperada → Recibida → Confirmada → Conciliación SENIAT → Aprobada → Declaración IVA → Declarada

FUNCIONALIDADES:
- Ciclo de vida completo: Esperado → Recibido → Confirmado → Conciliado → Declarado
- Referencia interna automática: RET-IVA-C/YYYY/NNNN (escalable a IVA-P, ISLR, MUN)
- Doble alícuota por comprobante (16% + 8% + Exento + No Gravada)
- Período quincenal nativo (1Q / 2Q) con detección automática
- Conciliación SENIAT: carga XLSX o vía RPA, estado visual con 16 combinaciones + retenciones pendientes de períodos anteriores
- Declaración IVA: desglose por 4 tasas (0%, 8%, 16%, 15%), Reporte 030, RPA
- Buzón de Comprobantes por Email: OCR automático + match por N° Control o RIF
- OCR: Claude Vision → Google Vision → Odoo IAP → Tesseract (fallback automático)
- Libro de Ventas: Excel 15 columnas (RLIVA Arts. 70-78)
- Recordatorios tipificados por email al cliente
- API RPA: 3 endpoints (extracción SENIAT, declaración, registro resultado)
- Asiento contable automático al confirmar

CONFIGURACIÓN REQUERIDA (ver README.md):
- Cuentas IVA: IVA Clientes → Configuración IVA (auto-configurado con cuentas 1151004 / 2172003 al instalar)
- OCR Claude: parámetro ve_retencion_iva.anthropic_api_key
- RPA: parámetros ve_retencion_iva.rpa_* (cuando esté disponible el robot)
    ''',
    'author': 'DJCS',
    'depends': ['account', 'mail', 'iap'],
    'data': [
        'security/ve_retencion_iva_security.xml',
        'security/ir.model.access.csv',
        'data/mail_alias_data.xml',
        'data/cron_data.xml',
        'data/sequences.xml',
        'report/ve_paperformat.xml',
        'report/ve_reporte_seniat.xml',
        'report/ve_comprobante_retencion.xml',
        'report/ve_comprobante_prov_retencion.xml',
        'report/ve_reporte_conciliacion.xml',
        'report/ve_libro_ventas.xml',
        'report/ve_libro_compras.xml',
        'wizards/wizard_subir_comprobante_views.xml',
        'wizards/wizard_carga_seniat_views.xml',
        'wizards/wizard_crear_periodo_views.xml',
        'wizards/wizard_declarado_mensual_views.xml',
        'wizards/wizard_conciliacion_smartiva_seniat_views.xml',
        'wizards/wizard_conciliacion_libro_ventas_views.xml',
        'wizards/wizard_reporte_ejecutivo_views.xml',
        'wizards/wizard_reset_demo_views.xml',
        'wizards/wizard_reset_piloto_views.xml',
        'wizards/wizard_setup_compania_views.xml',
        'wizards/wizard_libro_ventas_views.xml',
        'wizards/wizard_libro_compras_views.xml',
        'wizards/wizard_iva_proveedores_views.xml',
        'views/ve_wh_iva_prov_views.xml',
        'wizards/wizard_conciliar_confirm_views.xml',
        'wizards/wizard_declarar_views.xml',
        'wizards/wizard_declarar_prov_views.xml',
        'wizards/wizard_registrar_llamada_views.xml',
        'wizards/wizard_enviar_recordatorio_views.xml',
        'views/res_config_settings_views.xml',
        'views/account_move_views.xml',
        'views/ve_declaracion_iva_views.xml',
        'views/ve_conciliacion_prov_views.xml',
        'views/ve_dashboard_iva_views.xml',
        'views/ve_sancion_iva_views.xml',
        'wizards/wizard_estimacion_riesgo_views.xml',
        'views/ve_wh_iva_views.xml',
        'views/ve_seniat_retencion_views.xml',
        'views/ve_conciliacion_views.xml',
        'views/ve_comprobante_inbox_views.xml',
        'views/ve_wh_iva_cobranza_views.xml',
        'views/res_partner_views.xml',
        'views/ve_conecta_carga_ventas_views.xml',
        'views/ve_conecta_carga_compras_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            've_retencion_iva/static/src/css/seniat_periodo_list.css',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
    'post_init_hook': 'post_init_hook',
}
