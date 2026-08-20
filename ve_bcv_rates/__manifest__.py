{
    'name': 'Venezuela - Tasas BCV',
    'version': '19.0.1.0.1',
    'category': 'Accounting/Localizations',
    'summary': 'Actualización automática de tasas USD/EUR desde el BCV',
    'author': 'DJCS',
    'depends': ['base'],
    'data': [
        'data/ir_cron.xml',
        'data/ir_actions_server.xml',
        'views/res_currency_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
