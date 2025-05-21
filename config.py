# config.py
import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'), override=True) # Carga y sobrescribe variables de .env

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'una-clave-secreta-por-defecto-cambiar'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'nutriapp.db') # Usa nutriapp.db en la carpeta
    # print(f"DEBUG: SQLALCHEMY_DATABASE_URI = '{SQLALCHEMY_DATABASE_URI}'") # Eliminamos el debug
    BEDCA_API_KEY = os.environ.get('BEDCA_API_KEY') # Nueva configuración para BEDCA API Key
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
    DRIVE_FOLDER_ID = os.environ.get('DRIVE_FOLDER_ID')
    # Config email
    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True').lower() in ['true', 'on', '1']
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'False').lower() in ['true', 'on', '1']
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER') or os.environ.get('MAIL_USERNAME')

    # Constantes de la aplicación para formularios y lógica
    ACTIVITY_FACTORS = [
        (1.2, "Sedentario (poco o ningún ejercicio)"),
        (1.375, "Actividad Ligera (ejercicio 1-3 días/semana)"),
        (1.55, "Actividad Moderada (ejercicio 3-5 días/semana)"),
        (1.725, "Actividad Intensa (ejercicio 6-7 días/semana)"),
        (1.9, "Actividad Muy Intensa (trabajo físico + ejercicio)")
    ]
    AVAILABLE_PATHOLOGIES = [
        "Diabetes Tipo I", "Diabetes Tipo II HGO", "Diabetes Tipo II Insulinorequirente",
        "Hipertensión Arterial", "Dislipidemia", "Diverticulosis Colon",
        "Sínd. Intestino Irritable", "Reflujo Gastroesofágico", "Gastritis Crónica",
        "Insuf. Renal Leve-Moderada", "Insuf. Renal Severa",
        "Enf. Inflamatoria Intestinal", "Anemia", "Diabetes PreGestacional",
        "Diabetes Gestacional", "Diabetes Gestacional Insulinorequirente",
        "Trastorno Deglutorio", "Intestino Corto", "Megacolon", "SIBO",
        "Deporte Alto Rendimiento"
    ]
    EDUCATION_LEVELS = [
        ('primaria', 'Educación Primaria'),
        ('secundaria', 'Educación Secundaria'),
        ('universitario', 'Universitario / Terciario')
    ]
    PURCHASING_POWER_LEVELS = [
        ('bajo', 'Bajo'),
        ('medio', 'Medio'),
        ('medio-alto', 'Medio-Alto'),
        ('alto', 'Alto')
    ]
    DIET_TYPES = [
        ('Mediterranea', 'Mediterránea'),
        ('Cetogenica', 'Cetogénica'),
        ('Hiperproteica', 'Hiperproteica'),
        ('Paleo', 'Paleo'),
        ('Vegetariana', 'Vegetariana'),
        ('Ovolactovegetariana', 'Ovolactovegetariana'),
        ('Vegana', 'Vegana'),
        ('Otra', 'Otra (especificar)')
    ]
SEX_OPTIONS = [
    ('masculino', 'Masculino'),
    ('femenino', 'Femenino'),
    ('otro', 'Otro')
]