import os
import io
import json
import firebase_admin
import traceback
import smtplib
from datetime import datetime, timezone # Asegurar timezone importado
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import math
from functools import wraps

from flask import (
    Flask, request, render_template, redirect, url_for,
    session, flash, jsonify, send_file, abort, g
)
# Asumiendo Flask-Login se usará más adelante para el usuario
from flask_login import LoginManager, UserMixin, login_required, current_user, login_user, logout_user
from flask_wtf.csrf import CSRFProtect
from firebase_admin import credentials, auth
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from flask_migrate import Migrate
from urllib.parse import urlparse, urljoin

# Google / Gemini Imports
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.http import MediaIoBaseDownload # Asegúrate que esta línea esté
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError 
from googleapiclient.http import MediaIoBaseUpload
import re # Importar re para expresiones regulares

# ReportLab Imports
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.lib import colors

# Configuración Local
from config import Config
import logging # Import logging module

# --- Inicialización Flask y Extensiones ---
app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)
csrf = CSRFProtect(app)
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'
import logging
import subprocess

class CustomErrorHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        if record.levelno >= logging.ERROR:
            try:
                subprocess.run(["python", "export_last_traceback.py"])
            except Exception as e:
                print("Error al ejecutar el script de extracción de traceback:", e)

# Inicializar logging con el handler personalizado
handler = CustomErrorHandler('error.log')
handler.setLevel(logging.ERROR)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

app.logger.addHandler(handler)
app.logger.setLevel(logging.ERROR)

@app.errorhandler(Exception)
def manejar_excepcion(e):
    app.logger.exception("Excepción capturada:")
    return "Error interno del servidor", 500

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def is_safe_url(target: str) -> bool:
    """Return True if the URL is safe to redirect to."""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return (
        test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc
    )

# Explicitly set logger level for debugging
app.logger.setLevel(logging.DEBUG)
app.logger.info("Flask logger level set to DEBUG.") # Test log

try:
    if app.config['GEMINI_API_KEY']:
        genai.configure(api_key=app.config['GEMINI_API_KEY']) # type: ignore
        app.logger.info("INFO: API Key de Gemini configurada.")
    else:
        app.logger.warning("ADVERTENCIA: GEMINI_API_KEY no configurada en el archivo .env.")
    
    # Firebase Admin SDK Initialization
    cred_path = os.path.join(os.path.dirname(__file__), 'firebase-adminsdk.json')
    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        app.logger.info("INFO: Firebase Admin SDK inicializado desde archivo local 'firebase-adminsdk.json'.")
    else:
        app.logger.warning("ADVERTENCIA: Archivo 'firebase-adminsdk.json' no encontrado. La autenticación de API podría fallar.")
except Exception as config_error:
     print(f"ERROR: Configurando API de Gemini: {config_error}")

DRIVE_FOLDER_ID = app.config['DRIVE_FOLDER_ID']
if not DRIVE_FOLDER_ID:
     print("ADVERTENCIA: DRIVE_FOLDER_ID no configurado en el archivo .env.")

SCOPES = ['https://www.googleapis.com/auth/drive.file']
SERVICE_ACCOUNT_FILE = 'credentials.json'

# --- Decorador de Autenticación Firebase ---
def firebase_auth_required(view_func):
    """Verifica el token Firebase enviado en el encabezado Authorization."""

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Token de autenticación faltante"}), 401

        id_token = auth_header.split(" ", 1)[1]
        try:
            decoded = auth.verify_id_token(id_token)
        except Exception as e:  # pragma: no cover - logging
            app.logger.error(f"Error verificando token Firebase: {e}")
            return jsonify({"error": "Token inválido"}), 401

        firebase_uid = decoded.get("uid")
        user = User.query.filter_by(firebase_uid=firebase_uid).first()
        if not user:
            return jsonify({"error": "Usuario no registrado"}), 401

        g.user = user
        return view_func(*args, **kwargs)

    return wrapped_view

# --- Decorador de Autenticación Firebase para PACIENTES ---
def patient_auth_required(view_func):
    """
    Verifica el token de Firebase y asegura que el UID corresponda a un Paciente
    y que el paciente solo acceda a sus propios datos.
    """
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Token de autenticación de paciente faltante"}), 401

        id_token = auth_header.split(" ", 1)[1]
        try:
            decoded = auth.verify_id_token(id_token)
            firebase_uid = decoded.get("uid")
        except Exception as e:
            app.logger.error(f"Error verificando token Firebase de paciente: {e}")
            return jsonify({"error": "Token de paciente inválido o expirado"}), 401

        # Buscar al paciente en nuestra base de datos local por su UID de Firebase
        patient = Patient.query.filter_by(firebase_uid=firebase_uid).first()
        if not patient:
            return jsonify({"error": "Paciente no encontrado para este token"}), 401

        # **Paso de autorización crucial**
        # Asegurarse de que el ID del paciente en la URL coincida con el ID del paciente autenticado
        url_patient_id = kwargs.get('patient_id')
        if url_patient_id and patient.id != url_patient_id:
            app.logger.warning(f"Acceso denegado: Paciente UID {firebase_uid} (ID: {patient.id}) intentó acceder a datos del Paciente ID {url_patient_id}.")
            return jsonify({"error": "Acceso prohibido. Solo puedes acceder a tus propios datos."}), 403

        g.patient = patient  # Hacer que el objeto paciente esté disponible en la ruta
        return view_func(*args, **kwargs)

    return wrapped_view
# --- Procesador de Contexto ---
@app.context_processor
def inject_now():
    return {'now': datetime.now(timezone.utc)}

# --- Modelos de Base de Datos (SQLAlchemy) ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    firebase_uid = db.Column(db.String(128), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(100))
    surname = db.Column(db.String(100))
    profession = db.Column(db.String(100))
    license_number = db.Column(db.String(50))
    city = db.Column(db.String(100))
    country = db.Column(db.String(100))
    address = db.Column(db.String(200))
    phone_number = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    patients = db.relationship('Patient', backref='nutritionist', lazy=True)
    preparations = db.relationship('UserPreparation', back_populates='user', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'firebase_uid': self.firebase_uid,
            'email': self.email,
            'name': self.name,
            'surname': self.surname,
            'profession': self.profession,
            'license_number': self.license_number,
            'city': self.city,
            'country': self.country,
            'address': self.address,
            'phone_number': self.phone_number,
        }

# Modelo Patient (Datos relativamente estables)
class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    firebase_uid = db.Column(db.String(128), unique=True, nullable=True, index=True) # Vínculo con Firebase Auth
    # Identificación y Datos Filiatorios
    name = db.Column(db.String(80), nullable=False)
    surname = db.Column(db.String(80), nullable=False)
    cedula = db.Column(db.String(20), unique=True, index=True, nullable=False)
    dob = db.Column(db.Date)
    sex = db.Column(db.String(10))
    email = db.Column(db.String(120), unique=True, nullable=True, index=True)
    phone_number = db.Column(db.String(30), nullable=True)
    education_level = db.Column(db.String(50), nullable=True)
    purchasing_power = db.Column(db.String(50), nullable=True)
    height_cm = db.Column(db.Float) # Altura base (podría actualizarse raramente)

    # Alergias/Preferencias (estado más actual del paciente)
    allergies_json = db.Column(db.Text, default='[]')
    intolerances_json = db.Column(db.Text, default='[]')
    preferences_json = db.Column(db.Text, default='[]')
    aversions_json = db.Column(db.Text, default='[]')

    # Relación con sus evaluaciones (historial)
    evaluations = db.relationship('Evaluation', backref='patient', lazy='dynamic', order_by="desc(Evaluation.consultation_date)")

    # Métodos JSON (sin cambios)
    def _safe_json_loads(self, json_string):
        if not json_string: return []
        try:
            data = json.loads(json_string)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError): return []
    def get_list_field(self, field_name): return self._safe_json_loads(getattr(self, f"{field_name}_json", None))
    def set_list_field(self, field_name, data_list):
         if isinstance(data_list, list): cleaned_list = [str(item).strip() for item in data_list if str(item).strip()]
         elif isinstance(data_list, str): cleaned_list = [item.strip() for item in data_list.split(',') if item.strip()]
         else: cleaned_list = []
         setattr(self, f"{field_name}_json", json.dumps(cleaned_list))
    def get_allergies(self): return self.get_list_field('allergies')
    def set_allergies(self, data): self.set_list_field('allergies', data)
    def get_intolerances(self): return self.get_list_field('intolerances')
    def set_intolerances(self, data): self.set_list_field('intolerances', data)
    def get_preferences(self): return self.get_list_field('preferences')
    def set_preferences(self, data): self.set_list_field('preferences', data)
    def get_aversions(self): return self.get_list_field('aversions')
    def set_aversions(self, data): self.set_list_field('aversions', data)

    # Calcular edad (sin cambios)
    def calculate_age(self):
        if not self.dob: return None
        try:
            today = datetime.now(timezone.utc).date()
            age = today.year - self.dob.year - ((today.month, today.day) < (self.dob.month, self.dob.day))
            return age if age >= 0 else None
        except Exception: return None

# --- NUEVOS MODELOS PARA APP DE PACIENTE ---

class WeightEntry(db.Model):
    __tablename__ = 'weight_entry'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, default=lambda: datetime.now(timezone.utc).date())
    weight_kg = db.Column(db.Float, nullable=False)
    notes = db.Column(db.Text, nullable=True)

    patient = db.relationship('Patient', backref=db.backref('weight_history', lazy='dynamic'))

class ChatMessage(db.Model):
    __tablename__ = 'chat_message'
    id = db.Column(db.Integer, primary_key=True)
    evaluation_id = db.Column(db.Integer, db.ForeignKey('evaluation.id'), nullable=True, index=True) # Link to a specific evaluation/plan context, puede ser nulo para chat general
    sender_is_patient = db.Column(db.Boolean, nullable=False) # True si el paciente envió, False si el nutricionista
    message_text = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.now(timezone.utc))
    read_by_recipient = db.Column(db.Boolean, default=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False, index=True) # Quién es el paciente en esta conversación
    # user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True) # Quién es el nutricionista (si aplica)

    patient = db.relationship('Patient', backref=db.backref('chat_messages', lazy='dynamic'))
    evaluation = db.relationship('Evaluation', backref=db.backref('chat_messages', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'sender_is_patient': self.sender_is_patient,
            'message_text': self.message_text,
            'timestamp': self.timestamp.isoformat()
        }

# *** NUEVO MODELO: Evaluation (reemplaza a Plan) ***
class Evaluation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    consultation_date = db.Column(db.DateTime, index=True, default=lambda: datetime.now(timezone.utc))

    # Snapshot de Antropometría en esta evaluación
    weight_at_eval = db.Column(db.Float) # Renombrado
    wrist_circumference_cm = db.Column(db.Float)
    waist_circumference_cm = db.Column(db.Float)
    hip_circumference_cm = db.Column(db.Float)
    gestational_age_weeks = db.Column(db.Integer, default=0)

    # Snapshot de Nivel de actividad en esta evaluación
    activity_factor = db.Column(db.Float, default=1.2)

    # Snapshot de Variables Calculadas en esta evaluación
    calculated_imc = db.Column(db.Float)
    calculated_complexion = db.Column(db.String(20))
    calculated_waist_hip_ratio = db.Column(db.Float)
    calculated_waist_height_ratio = db.Column(db.Float)
    calculated_ideal_weight = db.Column(db.Float)
    calculated_calories = db.Column(db.Float) # GET

    # Snapshot de Riesgos Calculados en esta evaluación
    imc_risk = db.Column(db.String(10))
    whr_risk = db.Column(db.String(10))
    whtr_risk = db.Column(db.String(10))

    # Snapshot de Condiciones Clínicas en esta evaluación
    pathologies_json = db.Column(db.Text, default='[]') # Patologías seleccionadas
    other_pathologies_text = db.Column(db.Text) # Texto libre otras
    postoperative_text = db.Column(db.Text) # Texto libre postop

    # Snapshot de Dieta en esta evaluación
    diet_type = db.Column(db.String(100))
    other_diet_type_text = db.Column(db.Text)

    # Snapshot de Objetivos en esta evaluación
    target_weight = db.Column(db.Float)
    target_waist_cm = db.Column(db.Float)
    target_protein_perc = db.Column(db.Float)
    target_carb_perc = db.Column(db.Float)
    target_fat_perc = db.Column(db.Float)

    # Plan y Observaciones de esta evaluación
    edited_plan_text = db.Column(db.Text) # El plan textual (editado)
    user_observations = db.Column(db.Text) # Observaciones del profesional
    structured_plan_input_json = db.Column(db.Text) # JSON con TODOS los datos usados para generar el prompt

    # PDF en Drive para esta evaluación
    pdf_storage_path = db.Column(db.String(255), nullable=True) # Nombre de columna corregido para coincidir con la migración
    historical_pdf_ids_json = db.Column(db.Text, default='[]')
    # ——— Nuevos campos ———
    # Micronutrientes (guardados como dict JSON: {'Potasio': mg, 'Calcio': mg,})
    micronutrients_json = db.Column(db.Text, default='{}')
    # Alimentos base (lista de strings JSON)
    base_foods_json     = db.Column(db.Text, default='[]')
    # Campo para almacenar los rangos de referencia calculados para esta evaluación
    references_json = db.Column(db.Text, default='{}')


    # Métodos de ayuda para acceder/almacenar los campos
    def get_micronutrients(self):
        try:
            return json.loads(self.micronutrients_json)
        except:
            return {}
    def set_micronutrients(self, d: dict):
        self.micronutrients_json = json.dumps(d or {})

    def get_base_foods(self):
        try:
            return json.loads(self.base_foods_json)
        except:
            return []
    def set_base_foods(self, lst: list):
        self.base_foods_json = json.dumps(lst or [])

    @property
    def references(self):
        try:
            return json.loads(self.references_json)
        except:
            return {}

    @references.setter
    def references(self, value):
        self.references_json = json.dumps(value or {})

    # Métodos JSON (similar a Patient)
    def _safe_json_loads(self, json_string):
         if not json_string: return []
         try:
             data = json.loads(json_string)
             return data if isinstance(data, list) else []
         except (json.JSONDecodeError, TypeError): return []
    def get_pathologies(self): return self._safe_json_loads(self.pathologies_json)
    def set_pathologies(self, data_list):
         if isinstance(data_list, list): cleaned_list = [str(item).strip() for item in data_list if str(item).strip()]
         else: cleaned_list = []
         self.pathologies_json = json.dumps(cleaned_list)
    def get_historical_pdf_ids(self):
        try:
            return json.loads(self.historical_pdf_ids_json)
        except:
            return []

    def add_historical_pdf_id(self, old_pdf_id):
        if not old_pdf_id:
            return
        current_history = self.get_historical_pdf_ids()
        if old_pdf_id not in current_history: # Evitar duplicados si algo sale mal
            current_history.append(old_pdf_id)
            self.historical_pdf_ids_json = json.dumps(current_history)
# --- NUEVOS MODELOS: Ingredientes y Nutrientes ---

class Ingredient(db.Model):
    __tablename__ = 'ingredient'
    id = db.Column(db.Integer, primary_key=True)
    synonyms_json = db.Column(db.Text, default='[]') # Nuevo campo para sinónimos
    name = db.Column(db.String(255), nullable=False, unique=True, index=True)
    # Podrías añadir campos como common_unit, category, etc.

    # Relación con los datos nutricionales de este ingrediente (lazy='dynamic' es crucial)
    nutrients = db.relationship('IngredientNutrient', backref='ingredient', lazy='dynamic')
    # Relación con las equivalencias de unidades caseras
    equivalences = db.relationship('UnitEquivalence', backref='ingredient', lazy=True)

    def get_synonyms(self):
        try:
            syns = json.loads(self.synonyms_json)
            return [s.lower().strip() for s in syns if isinstance(s, str)]
        except (json.JSONDecodeError, TypeError):
            return []
    def set_synonyms(self, synonym_list):
        self.synonyms_json = json.dumps([str(s).strip() for s in synonym_list if str(s).strip()])

    def to_dict(self):
        nutri_data = self.nutrients.filter_by(reference_quantity=100.0, reference_unit='g').first()
        if not nutri_data:
            nutri_data = self.nutrients.first()

        return {
            'id': self.id,
            'name': self.name,
            'synonyms': self.get_synonyms(),
            'nutrients': {
                'calories': nutri_data.calories if nutri_data else None,
                'protein_g': nutri_data.protein_g if nutri_data else None,
                'carb_g': nutri_data.carb_g if nutri_data else None,
                'fat_g': nutri_data.fat_g if nutri_data else None,
            } if nutri_data else {}
        }

    def __repr__(self):
        return f'<Ingredient {self.id}: {self.name}>'

class IngredientNutrient(db.Model):
    __tablename__ = 'ingredient_nutrient'
    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('ingredient.id'), nullable=False, index=True)
    # Cantidad de referencia a la que se aplican los nutrientes (ej. 100.0 para por 100g)
    reference_quantity = db.Column(db.Float, nullable=False)
    reference_unit = db.Column(db.String(50), nullable=False) # Ej: 'g', 'ml', 'unidad'

    # Valores nutricionales por la cantidad de referencia
    calories = db.Column(db.Float, default=0.0)
    protein_g = db.Column(db.Float, default=0.0)
    carb_g = db.Column(db.Float, default=0.0)
    fat_g = db.Column(db.Float, default=0.0)
    # Puedes añadir columnas para micronutrientes específicos o usar un JSON/relación separada
    # Ejemplo: iron_mg = db.Column(db.Float, default=0.0)
    # O un campo JSON para flexibilidad:
    micronutrients_json = db.Column(db.Text, default='{}') # {'Hierro_mg': 5.0, 'VitaminaC_mg': 30.0}

    def get_micronutrients(self):
        try: return json.loads(self.micronutrients_json)
        except: return {}

class UserPreparation(db.Model):
    __tablename__ = 'user_preparation'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    ingredients_json = db.Column(db.Text, nullable=False, default='[]') # JSON string de ingredientes
    instructions = db.Column(db.Text, nullable=True)
    preparation_type = db.Column(db.String(50), nullable=True) # Ej: desayuno, almuerzo, cena, snack
    suitability_tags_json = db.Column(db.Text, nullable=False, default='[]') # JSON string de tags
    
    # --- Campos para información nutricional calculada por porción ---
    num_servings = db.Column(db.Float, default=1.0) # Número de porciones que rinde la receta total
    calories_per_serving = db.Column(db.Float, nullable=True)
    protein_g_per_serving = db.Column(db.Float, nullable=True)
    carb_g_per_serving = db.Column(db.Float, nullable=True)
    fat_g_per_serving = db.Column(db.Float, nullable=True)
    micronutrients_per_serving_json = db.Column(db.Text, default='{}') # JSON string de micronutrientes por porción

    source = db.Column(db.String(50), nullable=True, default='creada_usuario') # Ej: creada_usuario, favorita_ia
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))

    user = db.relationship('User', back_populates='preparations')

    def set_ingredients(self, ingredients_list):
        self.ingredients_json = json.dumps(ingredients_list)

    def get_ingredients(self):
        if self.ingredients_json:
            return json.loads(self.ingredients_json)
        return []

    def set_suitability_tags(self, tags_list):
        # Asegurarse de que sean strings y limpiar espacios
        cleaned_tags = [str(tag).strip().lower() for tag in tags_list if str(tag).strip()]
        self.suitability_tags_json = json.dumps(list(set(cleaned_tags))) # Guardar únicos y en minúsculas

    def get_suitability_tags(self):
        if self.suitability_tags_json:
            return json.loads(self.suitability_tags_json)
        return []
    
    def get_micronutrients_per_serving(self):
        try:
            return json.loads(self.micronutrients_per_serving_json)
        except:
            return {}

    def set_micronutrients_per_serving(self, d: dict):
        # Asegurarse de que sea un diccionario antes de guardar
        if isinstance(d, dict):
            self.micronutrients_per_serving_json = json.dumps(d)
        else: self.micronutrients_per_serving_json = '{}'

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'description': self.description,
            'ingredients': self.get_ingredients(),
            'instructions': self.instructions,
            'preparation_type': self.preparation_type,
            'suitability_tags': self.get_suitability_tags(),
            # --- Incluir campos nutricionales ---
            'num_servings': self.num_servings,
            'calories_per_serving': self.calories_per_serving,
            'protein_g_per_serving': self.protein_g_per_serving,
            'carb_g_per_serving': self.carb_g_per_serving,
            'fat_g_per_serving': self.fat_g_per_serving,
            'micronutrients_per_serving': self.get_micronutrients_per_serving(),
            'source': self.source,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<UserPreparation {self.id}: {self.name}>'

# --- NUEVO MODELO: Equivalencias de Unidades Caseras ---
class UnitEquivalence(db.Model):
    __tablename__ = 'unit_equivalence'
    id = db.Column(db.Integer, primary_key=True)
    ingredient_id = db.Column(db.Integer, db.ForeignKey('ingredient.id'), nullable=False, index=True)
    household_unit = db.Column(db.String(50), nullable=False) # Ej: 'taza', 'cucharada', 'unidad'
    grams_per_unit = db.Column(db.Float, nullable=False) # Gramos equivalentes para esa unidad
    # Podrías añadir un campo para ml_per_unit si aplica

    def __repr__(self):
        return f'<UnitEquivalence {self.id}: {self.household_unit} for Ingredient {self.ingredient_id}>'

# --- Función Auxiliar de Validación Numérica ---
def validate_numeric_field(value_str, field_name, type_converter=float, min_val=None, max_val=None, allowed_values=None):
    """
    Valida un campo numérico.
    Devuelve el valor convertido si es válido, o None si la entrada es None o vacía.
    Lanza ValueError con un mensaje específico si la validación falla.
    """
    if value_str is None:
        return None
    
    cleaned_value_str = str(value_str).strip()
    if not cleaned_value_str: # Si después de limpiar es una cadena vacía
        return None

    try:
        num_val = type_converter(cleaned_value_str)
    except ValueError:
        raise ValueError(f"El campo '{field_name}' debe ser un número válido (formato incorrecto: '{cleaned_value_str}').")

    if min_val is not None and num_val < min_val:
        raise ValueError(f"El valor para '{field_name}' ({num_val}) no puede ser menor que {min_val}.")
    if max_val is not None and num_val > max_val:
        raise ValueError(f"El valor para '{field_name}' ({num_val}) no puede ser mayor que {max_val}.")
    if allowed_values is not None and num_val not in allowed_values:
        allowed_str = ", ".join(map(str, allowed_values))
        raise ValueError(f"El valor para '{field_name}' ({num_val}) no es válido. Valores permitidos: {allowed_str}.")
    return num_val


# --- Funciones de Cálculo ---
def calculate_imc(weight_kg, height_cm):
    if not weight_kg or not height_cm or height_cm <= 0 or weight_kg <= 0: return None
    try:
        height_m = height_cm / 100.0
        imc = round(weight_kg / (height_m ** 2), 1)
        return imc
    except Exception: return None

def calculate_complexion(height_cm, wrist_cm, sex):
    if not height_cm or not wrist_cm or not sex or wrist_cm <= 0 or height_cm <= 0: return None
    try:
        r = height_cm / wrist_cm
        if sex.lower() == 'masculino':
            if r > 10.4: return 'pequena'
            elif r >= 9.6: return 'mediana'
            else: return 'grande'
        elif sex.lower() == 'femenino':
            if r > 11.0: return 'pequena'
            elif r >= 10.1: return 'mediana'
            else: return 'grande'
        else: return None
    except Exception: return None

def calculate_waist_hip_ratio(waist_cm, hip_cm):
    if not waist_cm or not hip_cm or hip_cm <= 0 or waist_cm <= 0: return None
    try: return round(waist_cm / hip_cm, 2)
    except Exception: return None

def calculate_waist_height_ratio(waist_cm, height_cm):
    if not waist_cm or not height_cm or height_cm <= 0 or waist_cm <= 0: return None
    try: return round(waist_cm / height_cm, 2)
    except Exception: return None

def calculate_ideal_weight_devine(height_cm, sex):
    if not height_cm or not sex or height_cm <= 0: return None
    try:
        height_inches = height_cm / 2.54
        if height_inches <= 60:
             min_ideal, max_ideal = calculate_ideal_weight_range_imc(height_cm) # Usar función auxiliar IMC
             return round((min_ideal + max_ideal) / 2, 1) if min_ideal else None
        inches_over_5_feet = height_inches - 60
        if sex.lower() == 'masculino': ideal_kg = 50 + (2.3 * inches_over_5_feet)
        elif sex.lower() == 'femenino': ideal_kg = 45.5 + (2.3 * inches_over_5_feet)
        else: return None
        return round(ideal_kg, 1)
    except Exception: return None

def calculate_ideal_weight_range_imc(height_cm): # Función auxiliar usada arriba
    if not height_cm or height_cm <= 0: return (None, None)
    try:
        height_m = height_cm / 100.0
        min_weight = round(18.5 * (height_m ** 2), 1)
        max_weight = round(24.9 * (height_m ** 2), 1)
        return (min_weight, max_weight)
    except Exception: return (None, None)


def adjust_ideal_weight_for_complexion(ideal_weight, complexion):
    if not ideal_weight or not complexion: return ideal_weight
    try:
        if complexion == 'grande': return round(ideal_weight * 1.1, 1)
        elif complexion == 'pequena': return round(ideal_weight * 0.9, 1)
        else: return ideal_weight
    except Exception: return ideal_weight

def assess_imc_risk(imc):
    if not imc: return None
    if imc < 18.5: return 'bajo'
    elif imc < 25: return 'bajo' # Normal se considera bajo riesgo nutricional general
    elif imc < 30: return 'medio'
    else: return 'alto'

def assess_whr_risk(whr, sex):
    if not whr or not sex: return None
    try:
        if sex.lower() == 'masculino':
            if whr >= 1.0: return 'alto'
            elif whr >= 0.9: return 'medio'
            else: return 'bajo'
        elif sex.lower() == 'femenino':
            if whr >= 0.85: return 'alto'
            elif whr >= 0.80: return 'medio'
            else: return 'bajo'
        else: return None
    except Exception: return None

def assess_whtr_risk(whtr):
    if not whtr: return None
    try:
        if whtr >= 0.6: return 'alto'
        elif whtr >= 0.5: return 'medio'
        else: return 'bajo'
    except Exception: return None

def calculate_tmb_mifflin(weight_kg, height_cm, age, sex):
    if not all([weight_kg, height_cm, age, sex]) or age <=0 or weight_kg <=0 or height_cm <=0: return None
    try:
        if sex.lower() == 'masculino': tmb = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) + 5
        elif sex.lower() == 'femenino': tmb = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) - 161
        else: return None
        return round(tmb)
    except Exception: return None
def calculate_get(tmb, activity_factor):
    if not tmb or not activity_factor or tmb <= 0 or activity_factor <= 0: return None
    try: return round(tmb * activity_factor)
    except Exception: return None

# --- Funciones Auxiliares (Gemini, PDF, Drive, Email) ---

def get_drive_service():
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        app.logger.error(f"ERROR: No se encontró '{SERVICE_ACCOUNT_FILE}'.")
        return None
    try:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds)
        app.logger.info("INFO: Servicio Google Drive autenticado.")
        return service
    except Exception as e:
        app.logger.error(f"ERROR: Creando servicio Drive: {e}")
        return None

def download_from_drive(file_id):
    """
    Downloads a file from Google Drive given its file ID.
    Returns a BytesIO buffer with the file content, or None on error.
    """
    service = get_drive_service()
    if not service:
        app.logger.error("ERROR: No se pudo obtener servicio Drive para descargar.")
        return None

    try:
        request = service.files().get_media(fileId=file_id)
        file_content_buffer = io.BytesIO()
        # Corregir el constructor de MediaIoBaseDownload
        downloader = MediaIoBaseDownload(fd=file_content_buffer, request=request, chunksize=1024*1024)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            if status: # El objeto status puede ser None si el chunk no tiene progreso para reportar
                 app.logger.debug(f"Descargando archivo {file_id}: {int(status.progress() * 100)}%.")

        file_content_buffer.seek(0) # Rewind the buffer
        app.logger.info(f"Archivo {file_id} descargado exitosamente de Drive.")
        return file_content_buffer
    except HttpError as error:
        app.logger.error(f'Error HttpError descargando de Drive (ID: {file_id}): {error}', exc_info=True)
        if error.resp.status == 404: # Específicamente para archivo no encontrado
            app.logger.error(f"Archivo con ID {file_id} no encontrado en Drive.")
        return None
    except Exception as e:
        app.logger.error(f'Error inesperado descargando de Drive (ID: {file_id}): {e}', exc_info=True)
        return None

def _clean_item_name_further(name_str: str) -> str:
    """Helper to perform final cleaning on a presumed item name."""
    original_input_for_log = name_str # Guardar el original para logging
    app.logger.debug(f"NUTR_CLEAN_DEBUG (_clean_item_name_further) - INPUT: '{name_str}'")
    
    # 1. Strip general whitespace and common list markers
    cleaned_name = name_str.lstrip('*-• ').strip()

    # Eliminar dos puntos al final y otros caracteres que podrían quedar pegados
    cleaned_name = cleaned_name.rstrip(':,*-.').strip()

    # 2. Remove specific parenthesized quantities/units if not caught by main parser
    cleaned_name = re.sub(r"\s*\((?:aprox\.\s*)?[\d.,/\s]+(?:g|ml|kg|l|unidad|taza|cucharada|cucharadita|pizca|porcion|rebanada|filete|pieza)[a-zA-ZáéíóúñÑ]*\s*\)$", "", cleaned_name, flags=re.IGNORECASE).strip()

    # Remove leading phrases like "bloque de", "diente de", etc.
    leading_phrases_to_remove = [
        "bloque de ", "diente de ", "loncha de ", "filete de ", 
        "cabeza de ", "ramita de ", "hojas de ", "trozo de "
    ]
    for phrase in leading_phrases_to_remove:
        if cleaned_name.lower().startswith(phrase):
            cleaned_name = cleaned_name[len(phrase):].strip()
            app.logger.debug(f"NUTR_CLEAN_DEBUG (_clean_item_name_further) - After removing leading phrase '{phrase}': '{cleaned_name}'")

    # 3. Remove general parenthetical explanations (e.g., (opcional), (escurrido))
    # This is more aggressive, so it's after specific quantity/unit removal.
    # Avoid removing if it's like "Maiz (Zea mays)" - check if content is mostly non-numeric/non-unit
    cleaned_name = re.sub(r"\s*\((?![^)]*Zea mays)(?:[^()]*?)\)$", lambda m: "" if not re.search(r'\d', m.group(0)) and not any(u in m.group(0).lower() for u in ['g','ml','kg','l','taza']) else m.group(0), cleaned_name, flags=re.IGNORECASE).strip()
    app.logger.debug(f"NUTR_CLEAN_DEBUG (_clean_item_name_further) - After removing general parentheses: '{cleaned_name}'")

    # Remove common trailing adjectives/phrases.
    # This list needs to be curated carefully.
    trailing_phrases_to_remove = [
        "cocidos", "cocidas", "crudos", "crudas", "picados", "picadas", "molidos", "molidas", "rallados", "ralladas", # Plurales
        "cocido", "cocida", "crudo", "cruda", "picado", "picada", "molido", "molida", "rallado", "rallada", # Singulares
        "en cubos", "en trozos", "en juliana", "en rodajas", "en floretes", "fileteado", "troceado", "troceada", "laminado", "laminada",
        "frescos", "frescas", "enteros", "enteras", "congelados", "congeladas", "secos", "secas", # Plurales
        "fresco", "fresca", "entero", "entera", "congelado", "congelada", "seco", "seca", # Singulares
        "medianos", "medianas", "pequeños", "pequeñas", # Plurales - 'grandes' puede ser parte de un nombre
        # "grandes", # Comentado porque "huevo grande" es válido
        "mediano", "mediana", "pequeño", "pequeña", "grande", "cortado", "cortada", "pelado", "pelada", "desmenuzado", "desmenuzada", "deshuesado", "deshuesada", # Singulares
        "maduro", "madura", "rallado", "rallada", r"etc\.", r"aprox\.",
        "sin piel", "con piel", "deshuesado", "deshuesada", "escurrido", "escurrida", "en conserva", "al natural", "en agua",
        "firme, prensado y", "firme", "prensado", "triturado", "virgen extra", "en lonchas", "asadas", "salteadas", "cocidas", "cocidos" # Específicos para casos vistos
    ]
    for phrase in trailing_phrases_to_remove:
        # Regex to match the phrase at the end, possibly preceded by a comma or space
        # Ensures we don't cut off part of a compound name if the phrase is in the middle.
        pattern = rf"^(.*?)(?:,\s*|\s+)\b{re.escape(phrase)}\b\s*$"
        match = re.match(pattern, cleaned_name, re.IGNORECASE)
        if match and match.group(1).strip(): 
            cleaned_name = match.group(1).strip()
            app.logger.debug(f"NUTR_CLEAN_DEBUG (_clean_item_name_further) - After removing trailing phrase '{phrase}': '{cleaned_name}'")
    
    # 4. Remove leading quantities like "1/2", "2", "10 unidades de"
    cleaned_name = re.sub(r"^\s*(?:\d+/\d+\s+|\d+\s+(?:unidad(?:es)?\s+de\s+)?)", "", cleaned_name, flags=re.IGNORECASE).strip()
    app.logger.debug(f"NUTR_CLEAN_DEBUG (_clean_item_name_further) - After removing leading quantities: '{cleaned_name}'")

    cleaned_name = re.sub(r"\s+\d+/\d+$", "", cleaned_name).strip()
    
    if cleaned_name.lower().startswith("de ") and len(cleaned_name.split()) > 2:
        cleaned_name = cleaned_name[3:].strip()
    # app.logger.debug(f"_clean_item_name_further - OUTPUT: '{cleaned_name.strip()}'") # Puede ser muy verboso
    return cleaned_name.strip()

def _parse_ingredient_line(line_text_with_star: str) -> dict:
    """
    Parses a single ingredient line to extract item, quantity, and unit.
    Prioritizes standardized units (g, ml) and common household units.
    """
    original_line = line_text_with_star
    text_to_parse = line_text_with_star.lstrip('*').strip()

    item_name = text_to_parse
    quantity = 0.0
    unit = "N/A" 
    
    std_parenthesized_match = re.search(
        r"\((?:aprox\.\s*)?([\d.,/\s]+)\s*([gmkltzcs]+[a-zA-ZáéíóúñÑ]*)\s*\)",
        text_to_parse,
        re.IGNORECASE
    )
    
    if std_parenthesized_match:
        qty_str_paren = std_parenthesized_match.group(1).strip()
        unit_str_paren = std_parenthesized_match.group(2).strip().lower()
        parsed_qty_from_paren = None
        parsed_unit_from_paren = None
        try:
            if '/' in qty_str_paren:
                num, den = map(float, qty_str_paren.split('/'))
                parsed_qty_from_paren = num / den if den != 0 else 0.0
            else:
                parsed_qty_from_paren = float(qty_str_paren.replace(',', '.'))

            if unit_str_paren in ['gramos', 'gr', 'grs', 'grm', 'g']: parsed_unit_from_paren = 'g'
            elif unit_str_paren in ['mililitros', 'ml', 'mls', 'mlt', 'cc']: parsed_unit_from_paren = 'ml'
            elif unit_str_paren in ['kilos', 'kg', 'kgs']: parsed_qty_from_paren *= 1000; parsed_unit_from_paren = 'g'
            elif unit_str_paren in ['litros', 'lts', 'l', 'litro']: parsed_qty_from_paren *= 1000; parsed_unit_from_paren = 'ml'
            else: 
                parsed_qty_from_paren = None 
                parsed_unit_from_paren = None
            
            if parsed_qty_from_paren is not None:
                definitive_quantity = parsed_qty_from_paren
                definitive_unit = parsed_unit_from_paren
                # Remove the matched parenthesized part AND any leading list markers for cleaning
                text_for_item_name_extraction = text_to_parse.replace(std_parenthesized_match.group(0), "").lstrip('*-• ').strip()
                
                item_name_final = _clean_item_name_further(text_for_item_name_extraction)

                app.logger.info(f"_parse_ingredient_line (Parenthesized Std Qty + Item Re-Parse): Item='{item_name_final}', Qty={definitive_quantity}, Unit='{definitive_unit}' from '{original_line}'")
                return {'item': item_name_final, 'quantity': definitive_quantity, 'unit': definitive_unit, 'original_line': original_line}
        except ValueError:
            app.logger.warning(f"_parse_ingredient_line - ValueError parsing parenthesized std quantity '{qty_str_paren}' from '{original_line}'.")

    qty_unit_pattern = r"([\d.,/\s]+)\s*([a-zA-ZáéíóúÁÉÍÓÚñÑ]+(?:\s+de)?)"
    match_A = re.match(rf"^{qty_unit_pattern}\s+(.+)", text_to_parse, re.IGNORECASE)
    match_B = re.match(rf"(.+?)\s+{qty_unit_pattern}$", text_to_parse, re.IGNORECASE)

    chosen_match = None
    item_name_candidate = text_to_parse 

    if match_A:
        chosen_match = match_A
        qty_str = chosen_match.group(1).strip()
        unit_str_raw = chosen_match.group(2).strip()
        item_name_candidate = chosen_match.group(3).lstrip('*-• ').strip() # Clean leading markers
    elif match_B:
        chosen_match = match_B
        item_name_candidate = chosen_match.group(1).lstrip('*-• ').strip() # Clean leading markers
        if item_name_candidate and item_name_candidate.endswith(':'):
            item_name_candidate = item_name_candidate[:-1].strip()
        qty_str = chosen_match.group(2).strip()
        unit_str_raw = chosen_match.group(3).strip()

    if chosen_match:
        try:
            if '/' in qty_str:
                num, den = map(float, qty_str.split('/'))
                parsed_quantity = num / den if den != 0 else 0.0
            else:
                parsed_quantity = float(qty_str.replace(',', '.'))
            
            quantity = parsed_quantity
            unit_str_cleaned_for_check = unit_str_raw.lower().replace(" de", "").strip()
            final_item_name_candidate = item_name_candidate
            final_unit = "N/A"

            known_units_map = {
                'g': 'g', 'gramos': 'g', 'gr': 'g', 'grs': 'g', 'grm': 'g',
                'ml': 'ml', 'mililitros': 'ml', 'mls': 'ml', 'mlt': 'ml', 'cc': 'ml',
                'kg': 'g', 'kilos': 'g', 'kgs': 'g', 
                'l': 'ml', 'litros': 'ml', 'lts': 'ml', 'litro': 'ml', 
                'cucharadita': 'cucharadita', 'cdta': 'cucharadita', 'cucharaditas': 'cucharadita', 'cucharadita de té': 'cucharadita', 'cucharadita te': 'cucharadita', 'cdté': 'cucharadita',
                'cucharada': 'cucharada', 'cda': 'cucharada', 'cucharadas': 'cucharada', 'cucharada sopera': 'cucharada', 'cs': 'cucharada', 'cdas': 'cucharada',
                'taza': 'taza', 'tz': 'taza', 'tazas': 'taza',
                'unidad': 'unidad', 'unidades': 'unidad', 'unid': 'unidad', 'u': 'unidad', 'un.': 'unidad',
                'pieza': 'pieza', 'piezas': 'pieza', 'pz': 'pieza',
                'filete': 'filete', 'filetes': 'filete',
                'rebanada': 'rebanada', 'rebanadas': 'rebanada',
                'porcion': 'porcion', 'porción': 'porcion', 'porciones': 'porcion'
            }
            normalized_unit_from_map = known_units_map.get(unit_str_cleaned_for_check)

            if normalized_unit_from_map:
                final_unit = normalized_unit_from_map
                if unit_str_cleaned_for_check in ['kg', 'kilos', 'kgs']: quantity *= 1000
                elif unit_str_cleaned_for_check in ['l', 'litros', 'lts', 'litro']: quantity *= 1000
            else:
                final_item_name_candidate = f"{unit_str_raw} {item_name_candidate}".strip()
                if quantity == 1.0 or quantity == 0.5 or (quantity > 0 and quantity < 2 and "/" in qty_str):
                    final_unit = "unidad"
                app.logger.debug(f"_parse_ingredient_line: Non-standard unit '{unit_str_raw}' treated as part of item. New item candidate: '{final_item_name_candidate}', New unit: '{final_unit}'.")

            item_name_to_return = _clean_item_name_further(final_item_name_candidate)
            app.logger.info(f"_parse_ingredient_line (General Pattern): Item='{item_name_to_return}', Qty={quantity}, Unit='{final_unit}' from '{original_line}'")
            return {'item': item_name_to_return, 'quantity': quantity, 'unit': final_unit, 'original_line': original_line}

        except ValueError:
            app.logger.warning(f"_parse_ingredient_line - ValueError parsing general quantity '{qty_str}' from '{original_line}'. Fallback.")
            item_name = _clean_item_name_further(text_to_parse)
            quantity = 0.0 
            unit = "N/A"   
            if not item_name and text_to_parse:
                item_name = text_to_parse.strip()
                app.logger.warning(f"_parse_ingredient_line (Fallback after General Pattern error, _clean_item_name_further resulted in empty, using raw): Item='{item_name}' from '{original_line}'")
            else:
                app.logger.info(f"_parse_ingredient_line (Fallback after General Pattern error): Item='{item_name}' from '{original_line}'")
            # No retornamos aquí, dejamos que caiga al fallback general si no se pudo parsear cantidad
            pass # Explicitly fall through

    # --- Fallback logic if no specific quantity/unit pattern matched above ---
    item_name_final_cleaned = _clean_item_name_further(text_to_parse)
    parsed_quantity_final = 0.0
    parsed_unit_final = "N/A"

    al_gusto_keywords = ["al gusto", "a gusto", "cantidad necesaria", "c.n.", "c/n", "a su gusto"]
    common_spices_for_pizca = [
        "sal y pimienta", "sal", "pimienta", "especias", "orégano", "comino", 
        "nuez moscada", "pimentón", "curry", "cúrcuma", "jengibre en polvo", 
        "ajo en polvo", "cebolla en polvo", "canela", "clavo molido", "laurel",
        "tomillo", "romero", "albahaca", "perejil seco", "cilantro seco" 
    ]

    original_text_lower = text_to_parse.lower()
    item_name_lower_for_check = item_name_final_cleaned.lower()

    is_explicitly_al_gusto = any(keyword in original_text_lower for keyword in al_gusto_keywords)
    is_common_spice_candidate_for_pizca = item_name_lower_for_check in common_spices_for_pizca

    if is_explicitly_al_gusto or is_common_spice_candidate_for_pizca:
        parsed_quantity_final = 1.0
        parsed_unit_final = "pizca"
        log_message_prefix = "Al Gusto/Spice Default"
    else:
        log_message_prefix = "Fallback/Simple Item"

    if not item_name_final_cleaned and text_to_parse: 
        item_name_final_cleaned = text_to_parse.strip()
        app.logger.warning(f"_parse_ingredient_line ({log_message_prefix}, _clean_item_name_further resulted in empty, using raw): Item='{item_name_final_cleaned}', Qty={parsed_quantity_final}, Unit='{parsed_unit_final}' from '{original_line}'")
    else:
        app.logger.info(f"_parse_ingredient_line ({log_message_prefix}): Item='{item_name_final_cleaned}', Qty={parsed_quantity_final}, Unit='{parsed_unit_final}' from '{original_line}'")

    return {'item': item_name_final_cleaned, 'quantity': parsed_quantity_final, 'unit': parsed_unit_final, 'original_line': original_line}



def parse_recipe_from_text(recipe_identifier, full_plan_text):
    """
    Parses a specific recipe's ingredients and instructions from the full AI-generated text.
    Assumes the text includes a '== RECETARIO DETALLADO ==' section.

    Args:
        recipe_identifier (str): Can be the recipe name (cleaned) OR the recipe number string (e.g., "N°5").
                                 Using the number string is more robust.
        full_plan_text (str): The complete text of the plan including the recetario.

    Returns a dict {'ingredients': [], 'instructions': '', 'description': ''}
    or None if the recipe is not found or parsing fails.
    """
    original_recipe_identifier_for_log = str(recipe_identifier) # Guardar para logging
    if not recipe_identifier or not full_plan_text:
        app.logger.warning("parse_recipe_from_text: recipe_identifier o full_plan_text vacío.")
        return None

    # Find the RECETARIO DETALLADO section
    recetario_start_match = re.search(r"== RECETARIO DETALLADO ==", full_plan_text, re.IGNORECASE)
    
    if not recetario_start_match:
        app.logger.warning("RECETARIO DETALLADO section not found in plan text.")
        return None

    recetario_text = full_plan_text[recetario_start_match.end():]
    recetario_text = recetario_text.lstrip() # Eliminar espacios/saltos de línea iniciales
    app.logger.debug(f"parse_recipe_from_text: recetario_text (limpio, primeros 200 chars): '{recetario_text[:200]}'")


    recipe_match = None
    actual_recipe_name_from_recetario = None
    recipe_block_text = None
    
    is_identifier_a_number_str = re.match(r"N°(\d+)", str(recipe_identifier), re.IGNORECASE)

    if is_identifier_a_number_str:
        recipe_num_str_exact = is_identifier_a_number_str.group(0) # Esto será "N°X"
        # Patrón para capturar (1) el título y (2) el cuerpo de la receta.
        recipe_pattern = re.compile(
            rf"Receta\s*{re.escape(recipe_num_str_exact)}:\s*([^\n]+)\n((?:.|\n)*?)(?=\n\s*Receta\s*(?:N°|No\.|N\.)?\s*\d+:|\Z)",
            re.DOTALL | re.IGNORECASE
        )
        app.logger.debug(f"parse_recipe_from_text: Buscando por NÚMERO: '{recipe_num_str_exact}' con patrón: '{recipe_pattern.pattern}' en recetario_text que empieza con: '{recetario_text[:50]}...'")
        recipe_match = recipe_pattern.search(recetario_text)
        if recipe_match:
            actual_recipe_name_from_recetario = recipe_match.group(1).strip()
            recipe_block_text = recipe_match.group(2).strip()
            app.logger.info(f"parse_recipe_from_text: Found by number '{recipe_identifier}'. Actual title: '{actual_recipe_name_from_recetario}'")
    
    # Si no se encontró por número O si el identificador no era numérico, intentar por nombre
    if not recipe_match:
        cleaned_recipe_name_for_search = str(recipe_identifier).strip() # Usar el identificador original si no era "N°X"
        escaped_recipe_name = re.escape(cleaned_recipe_name_for_search)
        # Este patrón asume que 'cleaned_recipe_name_for_search' es el título exacto después de "Receta N°X: "
        recipe_pattern_name_search = re.compile(
            rf"Receta\s*(?:N°|No\.|N\.)?\s*\d+:\s*{escaped_recipe_name}(?:[^\n]*)\n(.*?)(?=\n\s*Receta\s*(?:N°|No\.|N\.)?\s*\d+:|\Z)",
            re.DOTALL | re.IGNORECASE
        )
        app.logger.debug(f"parse_recipe_from_text: Buscando por NOMBRE: '{cleaned_recipe_name_for_search}' con patrón: '{recipe_pattern_name_search.pattern}'")
        recipe_match = recipe_pattern_name_search.search(recetario_text)
        if recipe_match:
            actual_recipe_name_from_recetario = cleaned_recipe_name_for_search # El identificador era el nombre
            recipe_block_text = recipe_match.group(1).strip()
            app.logger.info(f"parse_recipe_from_text: Found by name '{actual_recipe_name_from_recetario}'.")

    if not recipe_match or not recipe_block_text:
        app.logger.warning(f"Recipe with identifier '{original_recipe_identifier_for_log}' not found in RECETARIO DETALLADO section or format unexpected after all attempts.")
        return None

    ingredients_list = []
    instructions_text = ""
    description_text = "" 
    servings_text_parsed = "1 porción" 

    ingredients_match = re.search(r"Ingredientes\s*\(.*?\):\s*\n(.*?)(?=\nPreparación:|\Z)", recipe_block_text, re.DOTALL | re.IGNORECASE)
    if ingredients_match:
        ingredients_block = ingredients_match.group(1).strip()
        
        for line in ingredients_block.split('\n'):
            line = line.strip()
            if not line.startswith('*'): 
                continue
            
            parsed_ingredient = _parse_ingredient_line(line) # _parse_ingredient_line ya loguea
            ingredients_list.append(parsed_ingredient)


    instructions_match = re.search(r"Preparación:\s*\n(.*?)(?=\nCondimentos Sugeridos:|\nSugerencia de Presentación:|\Z)", recipe_block_text, re.DOTALL | re.IGNORECASE)
    if instructions_match:
        instructions_text = instructions_match.group(1).strip()

    servings_match_local = re.search(r"Porciones que Rinde:\s*(.*?)(?=\n)", recipe_block_text, re.IGNORECASE)
    if servings_match_local:
        servings_text_parsed = servings_match_local.group(1).strip()
        num_match = re.search(r"(\d+[\.,]?\d*)", servings_text_parsed)
        if num_match:
            try:
                servings_text_parsed = float(num_match.group(1).replace(',', '.'))
            except ValueError:
                app.logger.warning(f"No se pudo convertir '{num_match.group(1)}' a float para num_servings en receta '{actual_recipe_name_from_recetario}'. Usando texto original.")

    return {
        'name': actual_recipe_name_from_recetario,
        'ingredients': ingredients_list,
        'instructions': instructions_text,
        'description': description_text,
        'num_servings': servings_text_parsed 
    }



# --- Funciones para Cálculo Nutricional de Preparaciones ---

# !!! IMPORTANTE: Esta función ahora consulta la base de datos.
# !!! Asegúrate de haber poblado las tablas Ingredient, IngredientNutrient y UnitEquivalence.
def get_ingredient_nutritional_info(ingredient_item_name, quantity, unit):
    """
    Busca información nutricional para un ingrediente dado su nombre, cantidad y unidad
    consultando la base de datos.
    Retorna un diccionario con calorías, macros (g), y micros (dict) para la cantidad dada.
    """
    if not ingredient_item_name or quantity is None or quantity <= 0:
        app.logger.debug(f"NUTR_CALC_DEBUG (get_ingredient_nutritional_info): Invalid input for '{ingredient_item_name}' (qty: {quantity}). Returning zeros.")
        return {"calories": 0, "protein_g": 0, "carb_g": 0, "fat_g": 0, "micros": {}}
    
    final_cleaned_name_for_search = _clean_item_name_further(ingredient_item_name) 
    normalized_name_for_search = final_cleaned_name_for_search.lower().strip()
    app.logger.debug(f"NUTR_CALC_DEBUG (get_ingredient_nutritional_info): Original item (ahora es parsed_item_name): '{ingredient_item_name}', Final cleaned for search: '{final_cleaned_name_for_search}', Normalized for DB: '{normalized_name_for_search}', Qty: {quantity}, Unit: '{unit}'")

    ingredient = Ingredient.query.filter(Ingredient.name.ilike(normalized_name_for_search)).first()
    match_strategy = "Exact Match"

    if not ingredient:
        match_strategy = "Prefix Match"
        app.logger.debug(f"NUTR_CALC_DEBUG (get_ingredient_nutritional_info): {match_strategy} - Attempting for '{normalized_name_for_search}%'.")
        ingredient = Ingredient.query.filter(Ingredient.name.ilike(f"{normalized_name_for_search}%")).order_by(db.func.length(Ingredient.name)).first()
        if ingredient:
            app.logger.info(f"NUTR_CALC_INFO: Ingredient '{normalized_name_for_search}' found by {match_strategy} as '{ingredient.name}' (ID: {ingredient.id}).")

    if not ingredient:
        match_strategy = "Substring Match"
        app.logger.debug(f"NUTR_CALC_DEBUG (get_ingredient_nutritional_info): {match_strategy} - Attempting for '%{normalized_name_for_search}%'.")
        if len(normalized_name_for_search) > 3: 
            ingredient = Ingredient.query.filter(Ingredient.name.ilike(f"%{normalized_name_for_search}%")).order_by(db.func.length(Ingredient.name)).first()
        if ingredient:
            app.logger.info(f"NUTR_CALC_INFO: Ingredient '{normalized_name_for_search}' found by {match_strategy} as '{ingredient.name}' (ID: {ingredient.id}).")

    if not ingredient:
        match_strategy = "Synonym Match"
        app.logger.debug(f"NUTR_CALC_DEBUG (get_ingredient_nutritional_info): {match_strategy} - Attempting for '{normalized_name_for_search}'.")
        all_ingredients_with_synonyms = Ingredient.query.filter(Ingredient.synonyms_json.isnot(None)).all()
        for ing_with_syn in all_ingredients_with_synonyms:
            if normalized_name_for_search in ing_with_syn.get_synonyms():
                ingredient = ing_with_syn
                app.logger.info(f"NUTR_CALC_INFO: Ingredient '{normalized_name_for_search}' found by {match_strategy} as '{ingredient.name}' (ID: {ingredient.id}).")
                break
        
    # Attempt 5: Token-based matching if still no ingredient found
    if not ingredient and len(normalized_name_for_search.split()) > 1: # Only if multiple words
        match_strategy = "Token-based Match"
        app.logger.debug(f"NUTR_CALC_DEBUG (get_ingredient_nutritional_info): {match_strategy} - Attempting for '{normalized_name_for_search}'.")
        search_tokens = [token for token in normalized_name_for_search.split() if len(token) > 2] # Ignore very short tokens
        if search_tokens:
            first_token_query = Ingredient.query.filter(Ingredient.name.ilike(f"%{search_tokens[0]}%"))
            potential_matches = first_token_query.order_by(
                db.case((Ingredient.name.ilike(f"{search_tokens[0]}%"), 0), else_=1), # Prefer matches starting with token
                db.func.length(Ingredient.name) # Then prefer shorter names
            ).limit(5).all() # Limit to a few potential matches

            if potential_matches:
                # Basic dissimilarity check: if the found name is too different, log a stronger warning or discard.
                # This is a very simple heuristic.
                ingredient = potential_matches[0]
                if len(ingredient.name.split()) > len(search_tokens) + 2 or \
                   abs(len(ingredient.name) - len(normalized_name_for_search)) > 15: # Arbitrary length diff
                    app.logger.warning(f"NUTR_CALC_WARNING: {match_strategy} for '{normalized_name_for_search}' found '{ingredient.name}' (ID: {ingredient.id}), but it seems quite different. Using it cautiously.")
                else:
                    app.logger.info(f"NUTR_CALC_INFO: Ingredient '{normalized_name_for_search}' found by {match_strategy} (token: '{search_tokens[0]}') as '{ingredient.name}' (ID: {ingredient.id}).")
    
    if ingredient and match_strategy == "Exact Match": # Log exact matches too for completeness
         app.logger.info(f"NUTR_CALC_INFO: Ingredient '{normalized_name_for_search}' found by {match_strategy} as '{ingredient.name}' (ID: {ingredient.id}).")

    if not ingredient:
        app.logger.error(f"NUTR_CALC_ERROR: Ingredient '{normalized_name_for_search}' (from original '{ingredient_item_name}') NOT FOUND in DB after all attempts.")
        return {"calories": 0, "protein_g": 0, "carb_g": 0, "fat_g": 0, "micros": {}}
    
    app.logger.debug(f"NUTR_CALC_DEBUG (get_ingredient_nutritional_info): Using Ingredient ID: {ingredient.id} ('{ingredient.name}') for calculations based on search for '{normalized_name_for_search}'.")
    nutri_ref = IngredientNutrient.query.filter_by(ingredient_id=ingredient.id, reference_unit='g', reference_quantity=100.0).first()
    if not nutri_ref:
         nutri_ref = IngredientNutrient.query.filter_by(ingredient_id=ingredient.id).first()

    if not nutri_ref:
        app.logger.error(f"NUTR_CALC_ERROR: Nutrient reference data NOT FOUND for Ingredient ID: {ingredient.id} ('{ingredient.name}')")
        return {"calories": 0, "protein_g": 0, "carb_g": 0, "fat_g": 0, "micros": {}}
    
    app.logger.debug(f"NUTR_CALC_DEBUG (get_ingredient_nutritional_info): Found nutri_ref for ID {ingredient.id}. Ref Qty: {nutri_ref.reference_quantity}{nutri_ref.reference_unit}, Cal: {nutri_ref.calories}")
    quantity_in_ref_unit = convert_quantity_to_reference_unit(ingredient.id, quantity, unit, nutri_ref.reference_unit)

    if quantity_in_ref_unit is None or nutri_ref.reference_quantity <= 0:
         app.logger.error(f"NUTR_CALC_ERROR: Unit conversion FAILED for '{ingredient_item_name}' ('{quantity} {unit}' to '{nutri_ref.reference_unit}'). Result: {quantity_in_ref_unit}")
         return {"calories": 0, "protein_g": 0, "carb_g": 0, "fat_g": 0, "micros": {}}
    
    app.logger.debug(f"NUTR_CALC_DEBUG (get_ingredient_nutritional_info): Converted quantity for '{ingredient_item_name}' is {quantity_in_ref_unit} {nutri_ref.reference_unit}")
    factor = quantity_in_ref_unit / nutri_ref.reference_quantity
    scaled_micros = {k: (v * factor if isinstance(v, (int, float)) else v) for k, v in nutri_ref.get_micronutrients().items()}
    
    result = {
        "calories": round(nutri_ref.calories * factor, 2) if nutri_ref.calories is not None else 0.0,
        "protein_g": round(nutri_ref.protein_g * factor, 2) if nutri_ref.protein_g is not None else 0.0,
        "carb_g": round(nutri_ref.carb_g * factor, 2) if nutri_ref.carb_g is not None else 0.0,
        "fat_g": round(nutri_ref.fat_g * factor, 2) if nutri_ref.fat_g is not None else 0.0,
        "micros": scaled_micros
    }
    app.logger.debug(f"NUTR_CALC_DEBUG (get_ingredient_nutritional_info): Scaled nutrients for '{ingredient_item_name}': Cal={result['calories']}, P={result['protein_g']}, C={result['carb_g']}, F={result['fat_g']}")
    return result



# !!! IMPORTANTE: Esta es una función de EJEMPLO/PLACEHOLDER para conversión de unidades.
# !!! Debes reemplazarla con tu lógica real que use la tabla UnitEquivalence.
def convert_quantity_to_reference_unit(ingredient_id, quantity, unit, reference_unit):
    """
    Convierte una cantidad de una unidad de entrada (`unit`) a una unidad de referencia (`reference_unit`)
    para un `ingredient_id` específico, utilizando la tabla `UnitEquivalence`.

    Retorna la cantidad convertida en la `reference_unit` o None si la conversión no es posible.
    """
    if unit is None or reference_unit is None or quantity is None or quantity <= 0:
        app.logger.debug(f"convert_quantity: Entrada inválida - ing_id: {ingredient_id}, unit: {unit}, ref_unit: {reference_unit}, qty: {quantity}")
        return None

    unit_norm_initial = unit.lower().strip()
    ref_unit_norm = reference_unit.lower().strip() # La unidad de referencia de la BD ya debería ser estándar

    # Mapa para normalizar unidades de entrada del usuario a las formas canónicas
    # almacenadas en UnitEquivalence.household_unit (que son generadas por populate_equivalences.py)
    _UNIT_NORMALIZATION_MAP = {
        # A g/ml/kg/l (estos luego son manejados por lógica específica)
        "gramos": "g", "grs": "g", "grm": "g", "gr": "g", "g.": "g",
        "mililitros": "ml", "mls": "ml", "mlt": "ml", "cc": "ml",
        "kilos": "kg", "kgs": "kg", "kilogramo": "kg", "kilogramos": "kg",
        "litros": "l", "lts": "l", "litro": "l",

        # A "taza" (coincide con populate_equivalences.py)
        "tazas": "taza", "tzs": "taza", "tz": "taza",

        # A "cucharada sopera" (coincide con populate_equivalences.py)
        "cucharadas soperas": "cucharada sopera", "cda sopera": "cucharada sopera",
        "cdas soperas": "cucharada sopera", "cs": "cucharada sopera",
        "cucharada": "cucharada sopera",  # Asume que "cucharada" genérica implica "sopera"
        "cucharadas": "cucharada sopera",
        "cda": "cucharada sopera",
        "cdas": "cucharada sopera",

        # A "cucharadita de té" (coincide con populate_equivalences.py)
        "cucharaditas de te": "cucharadita de té", "cdta de te": "cucharadita de té",
        "cucharadita te": "cucharadita de té",
        "cucharaditas": "cucharadita de té", # Asume que "cucharadita" genérica implica "de té"
        "cucharadita": "cucharadita de té",
        "cdtas": "cucharadita de té",
        "cdta": "cucharadita de té",
        "cucharita": "cucharadita de té",
        "cucharitas": "cucharadita de té",

        # A "unidad mediana" (coincide con populate_equivalences.py)
        "unidades medianas": "unidad mediana", "unid mediana": "unidad mediana", "ud mediana": "unidad mediana",

        # A "rebanada mediana" (coincide con populate_equivalences.py)
        "rebanadas medianas": "rebanada mediana",

        # A "porción mediana" (coincide con populate_equivalences.py)
        "porciones medianas": "porción mediana",

        # Unidades genéricas (se buscarán tal cual si no están en el mapa,
        # o se mapean a su forma singular estándar si es necesario)
        "unidades": "unidad", "unids": "unidad", "un": "unidad", "ud": "unidad", # mapea a "unidad"
        "piezas": "pieza", "pzs": "pieza", "pz": "pieza", # mapea a "pieza"
        "filetes": "filete", # mapea a "filete"
        "rebanadas": "rebanada", # mapea a "rebanada"
        "porciones": "porcion", # mapea a "porcion"
    }
    
    unit_norm = _UNIT_NORMALIZATION_MAP.get(unit_norm_initial, unit_norm_initial)
    app.logger.debug(f"convert_quantity: IngID {ingredient_id}. Unidad entrada: '{unit}', Normalizada a: '{unit_norm}'. Ref: '{ref_unit_norm}'. Cantidad: {quantity}")
# Dentro de convert_quantity_to_reference_unit

    ingredient_db = Ingredient.query.get(ingredient_id)
    ingredient_name_lower = ingredient_db.name.lower() if ingredient_db else ""

    # Conversiones directas ml <-> g usando densidades asumidas
    if unit_norm == 'ml' and ref_unit_norm == 'g':
        density = 1.0 # Default para agua/líquidos similares
        if "aceite" in ingredient_name_lower:
            density = 0.92
        elif "salsa de soja" in ingredient_name_lower or "soya" in ingredient_name_lower : # ser más específico
            density = 1.18 # Densidad aproximada para salsa de soja
        elif "leche" in ingredient_name_lower:
            density = 1.03
        # Añadir más densidades si es necesario
        app.logger.info(f"convert_quantity: IngID {ingredient_id} ('{ingredient_name_lower}'). Convirtiendo {quantity}ml a g usando densidad {density} g/ml.")
        return quantity * density
    elif unit_norm == 'g' and ref_unit_norm == 'ml':
        density = 1.0 # Default para agua/líquidos similares
        if "aceite" in ingredient_name_lower:
            density = 0.92
        elif "salsa de soja" in ingredient_name_lower or "soya" in ingredient_name_lower:
            density = 1.18
        elif "leche" in ingredient_name_lower:
            density = 1.03
        app.logger.info(f"convert_quantity: IngID {ingredient_id} ('{ingredient_name_lower}'). Convirtiendo {quantity}g a ml usando densidad {density} g/ml.")
        return quantity / density if density != 0 else None

    # Lógica existente para kg/l y unidades caseras
    if unit_norm == 'kg':
        quantity *= 1000
        unit_norm = 'g'
        app.logger.debug(f"convert_quantity: IngID {ingredient_id}. Convertido kg a g. Nueva cantidad: {quantity}, unidad: {unit_norm}")
    elif unit_norm == 'l':
        quantity *= 1000
        unit_norm = 'ml'
        app.logger.debug(f"convert_quantity: IngID {ingredient_id}. Convertido l a ml. Nueva cantidad: {quantity}, unidad: {unit_norm}")

    if unit_norm == ref_unit_norm:
        app.logger.debug(f"convert_quantity: IngID {ingredient_id}. Unidades coinciden después de normalización ('{unit_norm}'). Retornando cantidad: {quantity}")
        return quantity

    # Handle "pizca" conversion to grams (assign a small, somewhat arbitrary value)
    if unit_norm == 'pizca' and ref_unit_norm == 'g':
        app.logger.debug(f"convert_quantity: IngID {ingredient_id}. Convirtiendo 'pizca' a gramos. Usando 0.5g por defecto.")
        return quantity * 0.5 # Asumimos que 1 pizca es ~0.5g. Ajusta si es necesario.

    equivalence_entry = UnitEquivalence.query.filter_by(
        ingredient_id=ingredient_id,
        household_unit=unit_norm
    ).first()

    if equivalence_entry and equivalence_entry.grams_per_unit is not None:
        grams_from_household = quantity * equivalence_entry.grams_per_unit
        app.logger.debug(f"convert_quantity: IngID {ingredient_id}, {quantity} {unit} ({unit_norm}) -> {grams_from_household}g via UnitEquivalence.")
        if ref_unit_norm == 'g':
            return grams_from_household
        elif ref_unit_norm == 'ml':
            # Convertido a gramos, ahora de gramos a ml (necesita densidad inversa)
            density_inv = 1.0 # Default para agua
            if "aceite" in ingredient_name_lower: density_inv = 0.92
            elif "salsa de soja" in ingredient_name_lower or "soya" in ingredient_name_lower: density_inv = 1.18
            elif "leche" in ingredient_name_lower: density_inv = 1.03
            app.logger.info(f"convert_quantity: IngID {ingredient_id} ('{ingredient_name_lower}'). Convirtiendo {grams_from_household}g a ml usando densidad {density_inv} g/ml (inversa).")
            return grams_from_household / density_inv if density_inv != 0 else None
        else:
            app.logger.warning(f"convert_quantity: IngID {ingredient_id}. Convertido a {grams_from_household}g desde unidad casera, pero la unidad de referencia es '{ref_unit_norm}', no 'g' ni 'ml'.")
            return None

    # Manejar 'unidad' como unidad de referencia (si la unidad de entrada también es 'unidad' después de normalizar)
    if ref_unit_norm == 'unidad' and unit_norm == 'unidad':
        app.logger.debug(f"convert_quantity: IngID {ingredient_id}. Unidad de entrada y referencia son 'unidad'. Retornando cantidad: {quantity}")
        return quantity

    app.logger.warning(f"convert_quantity: No se encontró regla de conversión para IngID {ingredient_id} de '{quantity} {unit}' (normalizado a '{unit_norm}') a '{reference_unit}' (normalizado a '{ref_unit_norm}').")
    return None


def calculate_total_nutritional_info(ingredients_list):
    """
    Calcula la información nutricional total para una lista de ingredientes de receta.
    Suma las calorías, macros y combina/suma los micros.
    """
    total_calories = 0.0
    total_protein_g = 0.0
    total_carb_g = 0.0
    total_fat_g = 0.0
    total_micros = {} # Diccionario para sumar micronutrientes (requiere manejo de unidades)

    if not isinstance(ingredients_list, list):
        app.logger.error(f"NUTR_CALC_DEBUG (calculate_total_nutritional_info): Received invalid type for ingredients_list: {type(ingredients_list)}")
        return {"calories": 0.0, "protein_g": 0.0, "carb_g": 0.0, "fat_g": 0.0, "micros": {}}
    app.logger.debug(f"NUTR_CALC_DEBUG (calculate_total_nutritional_info): Type is OK for ingredients_list: {type(ingredients_list)}")
    
    app.logger.debug(f"NUTR_CALC_DEBUG (calculate_total_nutritional_info): Calculating for ingredients_list: {ingredients_list}")
    for ingredient in ingredients_list: # Corregido: ingredient_data -> ingredient
        if isinstance(ingredient, dict): # Nueva estructura esperada
            # Usar 'parsed_item_name' para la búsqueda, y 'quantity', 'unit' que vienen del frontend/guardado.
            # 'original_description' es solo para mostrar al usuario.
            item_name_to_search = ingredient.get('parsed_item_name')
            original_desc_for_log = ingredient.get('original_description', item_name_to_search)
            quantity_str = ingredient.get('quantity')
            unit = ingredient.get('unit')
            
            try: quantity = float(quantity_str) if quantity_str is not None else 0.0
            except (ValueError, TypeError): quantity = 0.0

            if not item_name_to_search: # Si parsed_item_name está vacío, no podemos buscar
                app.logger.warning(f"NUTR_CALC_DEBUG: 'parsed_item_name' faltante o vacío para el ingrediente: {ingredient}. Saltando.")
                continue
            nutri_info = get_ingredient_nutritional_info(item_name_to_search, quantity, unit)
            total_calories += nutri_info.get("calories", 0.0)
            total_protein_g += nutri_info.get("protein_g", 0.0)
            total_carb_g += nutri_info.get("carb_g", 0.0)
            total_fat_g += nutri_info.get("fat_g", 0.0)
            # Sumar micronutrientes
            for micro_key, micro_value in nutri_info.get("micros", {}).items():
                if isinstance(micro_value, (int, float)): # Only sum if numeric
                    total_micros[micro_key] = total_micros.get(micro_key, 0.0) + micro_value
                else: # If not numeric, just store/overwrite
                    total_micros[micro_key] = micro_value
        else:
            app.logger.error(f"NUTR_CALC_DEBUG (calculate_total_nutritional_info): Elemento inesperado en ingredients_list (no es dict): {ingredient}")


    final_totals = {
        "calories": round(total_calories, 2),
        "protein_g": round(total_protein_g, 2),
        "carb_g": round(total_carb_g, 2),
        "fat_g": round(total_fat_g, 2),
        "micros": total_micros 
    }
    app.logger.debug(f"NUTR_CALC_DEBUG (calculate_total_nutritional_info): Final totals: Cal={final_totals['calories']}, P={final_totals['protein_g']}, C={final_totals['carb_g']}, F={final_totals['fat_g']}")
    return final_totals

# Helper function to format base_foods for the prompt
# Defined at module level to ensure it's available when generar_estructura_plan_prompt is called.
def format_base_foods_for_prompt(base_foods_list):
    if not base_foods_list:
        return "Ninguna especificada."
    
    formatted_string = ""
    for prep_data in base_foods_list:
        if isinstance(prep_data, dict): # Assumes the new detailed format
            name = prep_data.get('name', 'Preparación sin nombre')
            original_ingredients = prep_data.get('original_ingredients', [])
            ingredients_str_parts = []
            if original_ingredients:
                for ing in original_ingredients: # ing is expected to be like {'item': 'nombre_ingrediente'}
                    item_name = ing.get('item', 'N/A')
                    ingredients_str_parts.append(item_name)
                ingredients_desc = f" (Ingredientes base originales: {', '.join(ingredients_str_parts)})" if ingredients_str_parts else ""
            else:
                ingredients_desc = " (Ingredientes originales no detallados)"
            formatted_string += f"*   **{name}**{ingredients_desc}\n"
        elif isinstance(prep_data, str): # Maintains compatibility if only the name is sent
            formatted_string += f"*   **{prep_data}** (Ingredientes originales no detallados)\n"
            
    return formatted_string.strip() if formatted_string else "Ninguna especificada."

def generar_estructura_plan_prompt(plan_input_data):
    """Prepara el prompt para generar la ESTRUCTURA del plan nutricional."""
    # Recolección y formateo de datos para el prompt
    pathologies_str = ', '.join(plan_input_data.get('pathologies', [])) or 'Ninguna'
    if plan_input_data.get('other_pathologies_text'):
        pathologies_str += f", {plan_input_data.get('other_pathologies_text')}"
    if not pathologies_str or pathologies_str.isspace():
        pathologies_str = "Ninguna especificada"

    postop_str = plan_input_data.get('postoperative_text') or "No aplica"
    allergies_str = ', '.join(plan_input_data.get('allergies', [])) or 'Ninguna conocida'
    intolerances_str = ', '.join(plan_input_data.get('intolerances', [])) or 'Ninguna conocida'
    preferences_str = ', '.join(plan_input_data.get('preferences', [])) or 'Ninguna especificada'
    aversions_str = ', '.join(plan_input_data.get('aversions', [])) or 'Ninguna especificada'
    
    diet_type_str = plan_input_data.get('diet_type')
    if plan_input_data.get('other_diet_type_text'):
        diet_type_str = plan_input_data.get('other_diet_type_text')
    if not diet_type_str:
        diet_type_str = "General saludable"

    # --- Sección para identificar restricciones críticas por patología ---
    critical_dietary_restrictions_notes = []
    patient_pathologies_lower = [p.lower() for p in plan_input_data.get('pathologies', [])]

    if "insuf. renal severa" in patient_pathologies_lower:
        protein_target_renal = f"0.6-0.8 g/kg de peso actual ({round(0.6*plan_input_data.get('weight_at_plan', 0),1)}g - {round(0.8*plan_input_data.get('weight_at_plan', 0),1)}g)"
        critical_dietary_restrictions_notes.append(
            f"**INSUFICIENCIA RENAL SEVERA DETECTADA:** MÁXIMA PRIORIDAD a restricciones. "
            f"Proteínas: ESTRICTAMENTE {protein_target_renal}. "
            f"Potasio: LÍMITE ESTRICTO a {plan_input_data.get('micronutrients',{}).get('potassium_mg','N/A')} mg/día. "
            f"Fósforo: RESTRINGIR (evitar integrales, lácteos en exceso, legumbres en exceso). "
            f"Sodio: RESTRINGIR según Sección E. "
            f"Alcanzar GET con carbohidratos y grasas permitidas."
        )
    # Puedes añadir más bloques 'elif' o 'if' para otras patologías con restricciones críticas aquí
    # Ejemplo:
    # if "celiaquía" in patient_pathologies_lower: # Asumiendo que "celiaquía" es un valor posible
    #     critical_dietary_restrictions_notes.append(
    #         "**CELIAQUÍA DETECTADA:** MÁXIMA PRIORIDAD. Dieta ESTRICTAMENTE LIBRE DE GLUTEN (trigo, avena, cebada, centeno y derivados)."
    #     )
    
    critical_restrictions_prompt_section = ""
    if critical_dietary_restrictions_notes:
        critical_restrictions_prompt_section = (
            "\n\n    **H. RESTRICCIONES DIETÉTICAS CRÍTICAS POR PATOLOGÍA (MÁXIMA PRIORIDAD ABSOLUTA):**\n" +
            "\n".join([f"    *   {note}" for note in critical_dietary_restrictions_notes]) +
            "\n    *   Estas restricciones DEBEN CUMPLIRSE ESTRICTAMENTE y tienen prioridad sobre el 'Tipo de Dieta Base' general si hay conflicto. Adapta la selección de alimentos y preparaciones para cumplir estas reglas ANTES que nada."
        )

    macros_obj = f"P: {plan_input_data.get('target_protein_perc', 'N/A')}%, C: {plan_input_data.get('target_carb_perc', 'N/A')}%, G: {plan_input_data.get('target_fat_perc', 'N/A')}%"
    
    gestational_age_prompt = plan_input_data.get('gestational_age_weeks', 0)
    if gestational_age_prompt is None or str(gestational_age_prompt).strip() == '' or int(gestational_age_prompt) == 0:
        gestational_age_display = 'No aplica'
    else:
        gestational_age_display = f"{gestational_age_prompt} semanas"

    prompt = f"""
    Eres un asistente experto en nutrición clínica altamente capacitado. Tu tarea es generar un BORRADOR de la ESTRUCTURA de un plan nutricional SEMANAL DETALLADO y PERSONALIZADO para ser utilizado y modificado por un Licenciado en Nutrición.
    El plan debe basarse estrictamente en la siguiente información integral. Sé preciso, claro y considera TODAS las variables.
    Este plan NO debe incluir las recetas detalladas, solo los NOMBRES de los platos para almuerzos y cenas, indicando que la receta se verá aparte (ej. "Ver Receta N°X").

    **A. DATOS PACIENTE:**
    * Edad: {plan_input_data.get('age', 'N/A')} años; Sexo: {plan_input_data.get('sex', 'N/A')}
    * Nivel Educativo: {plan_input_data.get('education_level', 'N/E')}; Poder Adquisitivo: {plan_input_data.get('purchasing_power', 'N/E')}
    * Edad Gestacional: {gestational_age_display}

    **B. ANTROPOMETRÍA (Consulta):**
    * Altura: {plan_input_data.get('height_cm', 'N/A')} cm; Peso Actual: {plan_input_data.get('weight_at_plan', 'N/A')} kg
    * P. Muñeca: {plan_input_data.get('wrist_circumference_cm', 'N/A')} cm; P. Cintura: {plan_input_data.get('waist_circumference_cm', 'N/A')} cm; P. Cadera: {plan_input_data.get('hip_circumference_cm', 'N/A')} cm
    * IMC: {plan_input_data.get('calculated_imc', 'N/A')} (Riesgo: {plan_input_data.get('imc_risk', 'N/A')})
    * ICC: {plan_input_data.get('calculated_waist_hip_ratio', 'N/A')} (Riesgo: {plan_input_data.get('whr_risk', 'N/A')})
    * ICA: {plan_input_data.get('calculated_waist_height_ratio', 'N/A')} (Riesgo: {plan_input_data.get('whtr_risk', 'N/A')})
    * Peso Ideal (Est.): {plan_input_data.get('calculated_ideal_weight', 'N/A')} kg
    * **GET (Gasto Energético Total Estimado): {plan_input_data.get('calculated_calories', 'N/A')} kcal (TMB: {plan_input_data.get('tmb', 'N/A')} kcal, Factor Act: {plan_input_data.get('activity_factor', 'N/A')}) - ¡ESTE ES EL OBJETIVO CALÓRICO DIARIO ESTRICTO DEL PLAN!**

    **C. CONDICIONES CLÍNICAS Y CONTEXTO:**
    * Patologías (Evaluación): {pathologies_str}
    * Postoperatorio: {postop_str}
    * Alergias: {allergies_str}; Intolerancias: {intolerances_str}
    * Preferencias: {preferences_str}; Aversiones: {aversions_str}

    **D. DIETA Y OBJETIVOS:**
    * **Tipo Dieta Base (GUÍA PRINCIPAL Y OBLIGATORIA): {diet_type_str}** - El plan DEBE reflejar fielmente los principios, alimentos, métodos de cocción y combinaciones típicas de esta dieta en TODAS las comidas. **Las sugerencias de comidas deben ser ejemplos característicos, VARIADOS y apetecibles de la gastronomía asociada a este tipo de dieta. EVITA ABSOLUTAMENTE sugerencias de comidas que, aunque nutricionalmente puedan ser completas, resulten extrañas, poco palatables o no tengan lógica culinaria dentro del contexto de una alimentación normal y placentera.**
    * Objetivo Peso: {plan_input_data.get('target_weight', 'N/A')} kg; Objetivo Cintura: {plan_input_data.get('target_waist_cm', 'N/A')} cm
    * **Macros Objetivo (DISTRIBUCIÓN ESTRICTA): {macros_obj}** - La ingesta diaria debe cumplir esta distribución porcentual de macronutrientes. 
    * **NOTA SOBRE MACROS Y ACTIVIDAD FÍSICA:** Si el paciente realiza actividad física regular (Factor Actividad > 1.3), asegurar un aporte de carbohidratos de al menos 3-5 g/kg de peso corporal actual/día, a menos que una restricción crítica por patología (Sección H) o un tipo de dieta explícitamente bajo en carbohidratos (ej. cetogénica) lo impida. Si se especifica "Dieta Hiperproteica", el objetivo es 1.8-2.2 g de proteína/kg de peso objetivo/día, pero balanceado con el mínimo de carbohidratos mencionado y grasas saludables para alcanzar el GET.

    **E. LÍMITES DE MICRONUTRIENTES (¡CUMPLIMIENTO ESTRICTO REQUERIDO!):**
    * Potasio (K): {plan_input_data.get('micronutrients',{}).get('potassium_mg','N/A')} mg (LÍMITE MÁXIMO DIARIO TOTAL)
    * Calcio (Ca): {plan_input_data.get('micronutrients',{}).get('calcium_mg','N/A')} mg (OBJETIVO DIARIO)
    * Sodio (Na): {plan_input_data.get('micronutrients',{}).get('sodium_mg','N/A')} mg (LÍMITE MÁXIMO DIARIO TOTAL)
    * Colesterol: {plan_input_data.get('micronutrients',{}).get('cholesterol_mg','N/A')} mg (LÍMITE MÁXIMO DIARIO TOTAL)
    * **NOTA CRÍTICA:** Es IMPERATIVO que el contenido total diario de Potasio, Sodio y Colesterol del plan NO EXCEDA los límites máximos especificados. Selecciona alimentos y ajusta cantidades meticulosamente para cumplir estos límites.

    **F. PREPARACIONES SUGERIDAS POR EL USUARIO (Intentar incluir algunas, adaptando cantidades):**
    {format_base_foods_for_prompt(plan_input_data.get('base_foods',[]))}
    **(Estas son preparaciones de preferencia del usuario. Si las incluyes, intenta mantener la ESENCIA de sus ingredientes originales listados, pero DEBES AJUSTAR LAS CANTIDADES de cada ingrediente para que el plato final y el día completo cumplan ESTRICTAMENTE con los objetivos calóricos (Sección B), de macronutrientes (Sección D) y micronutrientes (Sección E) del paciente. No te limites solo a estas preparaciones; el plan debe ser variado.)**

    **G. CONSIDERACIONES ESPECIALES DE TEXTURA Y CONSISTENCIA (SI APLICA):**
    Si se indican patologías como "Trastorno Deglutorio", "Intestino Corto" o condiciones postoperatorias que requieran modificaciones de textura (ej. "{plan_input_data.get('postoperative_text', 'N/A')}", "{', '.join([p for p in plan_input_data.get('pathologies', []) if 'deglutorio' in p.lower() or 'gastrectomía' in p.lower() or 'intestino corto' in p.lower()])}"), TODAS las comidas (incluyendo desayunos y colaciones) deben ser de consistencia blanda, puré, o líquida según sea apropiado para la condición. Para "Intestino Corto", además de la textura, considera comidas más pequeñas y frecuentes si es necesario, y una buena hidratación.
    **IMPORTANTE PARA TEXTURAS MODIFICADAS Y CONDICIONES COMPLEJAS:** Aunque la textura deba ser modificada o haya condiciones como "Intestino Corto" o "Diabetes Gestacional", es ABSOLUTAMENTE CRÍTICO que el plan CUMPLA con el GET (Gasto Energético Total Estimado - Sección B) y la distribución de MACROS OBJETIVO (Sección D).
        *   **PRIORIDAD ABSOLUTA AL GET Y MACROS:** Ajusta las cantidades de los ingredientes en las preparaciones (incluso si son blandas o purés) para alcanzar el GET diario. Esto es más importante que mantener porciones pequeñas si el GET no se cumple.
        *   Para lograr esto con texturas blandas/puré:
            *   Enriquece purés y cremas con fuentes concentradas de calorías y proteínas (ej. aceite de oliva, yema de huevo cocida y tamizada, proteína en polvo sin sabor, leche en polvo descremada, quesos cremosos blandos si son permitidos por otras restricciones).
            *   Sugiere batidos nutricionalmente densos.
            *   Ajusta el volumen de las porciones si es necesario, pero prioriza alcanzar los objetivos numéricos de calorías y macronutrientes. **SI HAY RESTRICCIONES PROTEICAS SEVERAS (ver Sección H si aplica), EL ENRIQUECIMIENTO PROTEICO NO ES UNA OPCIÓN; el GET debe alcanzarse con grasas y carbohidratos permitidos.**
            *   Las preparaciones deben seguir siendo apetecibles y variadas dentro de las limitaciones de textura.
        *   Para "Diabetes Gestacional": Controla el tipo y cantidad de carbohidratos en cada comida, distribuyéndolos a lo largo del día. Prefiere carbohidratos complejos y fibra. Aun así, el GET total y la distribución de macros deben cumplirse.
        *   Si hay un conflicto entre mantener una textura muy específica y alcanzar los objetivos calóricos/proteicos, prioriza los objetivos numéricos y sugiere la adaptación de textura más cercana posible que permita alcanzar dichos objetivos.
    {critical_restrictions_prompt_section}

    **INSTRUCCIONES PARA LA GENERACIÓN DE LA ESTRUCTURA DEL PLAN:**
    1.  **Estructura Semanal:** Plan de Lunes a Domingo, detallando Desayuno, Colación de Mañana, Almuerzo, Colación de Tarde y Cena.
    2.  **Contenido de Comidas:**
        *   Para cada comida (incluyendo desayunos y colaciones), especifica los alimentos y cantidades APROXIMADAS.
        *   **La suma diaria de calorías DEBE COINCIDIR ESTRICTAMENTE con el GET (Sección B).**
        *   **PRIORIDAD MÁXIMA: La distribución de macronutrientes diaria DEBE COINCIDIR ESTRICTAMENTE con los Macros Objetivo (Sección D), A MENOS QUE la Sección H (Restricciones Críticas por Patología) especifique un objetivo proteico diferente (ej. para insuficiencia renal), en cuyo caso, ESE OBJETIVO PROTEICO DE LA SECCIÓN H TIENE PRIORIDAD. Considerar la "NOTA SOBRE MACROS Y ACTIVIDAD FÍSICA" de la Sección D para asegurar suficientes carbohidratos si aplica.**
        *   **TODAS las sugerencias de comidas, desde el desayuno hasta la cena, deben ser COHERENTES con el Tipo de Dieta Base (Sección D), APETECIBLES, CULINARIAMENTE LÓGICAS, VARIADAS y presentadas de forma atractiva, no como meras listas de ingredientes aislados. Considerar la Sección G si aplica. INCLUIR VARIEDAD DE FRUTAS Y VERDURAS DE DIFERENTES COLORES a lo largo de la semana para asegurar un buen aporte de micronutrientes. Considerar fuentes de calcio y vitamina D.**
    3.  **VARIEDAD EN DESAYUNOS:** Los desayunos deben ser variados a lo largo de la semana, ofreciendo diferentes tipos de preparaciones que se alineen con el Tipo de Dieta Base. EVITA repetir el mismo tipo de desayuno (ej. "tortilla") todos los días o la mayoría de los días. Propón alternativas creativas y nutricionalmente adecuadas.
    4.  **NOMBRES DE PLATOS PARA ALMUERZOS Y CENAS:**
        *   Para almuerzos y cenas, proporciona un NOMBRE DE PLATO descriptivo y atractivo.
        *   A continuación del nombre del plato, añade la referencia "(Ver Receta N°X)", donde X es un número consecutivo único para cada plato principal que requiera receta a lo largo de la semana. Comienza con N°1.
        *   Ejemplo: "Almuerzo: Salmón al horno con espárragos y quinoa (Ver Receta N°1)"
        *   Ejemplo: "Cena: Lentejas estofadas con verduras (Ver Receta N°2)"
        *   Asegura que haya VARIEDAD en los nombres de los platos de almuerzos y cenas a lo largo de la semana.
    5.  **Restricciones CLAVE:**
        *   **ALERGIAS E INTOLERANCIAS (Sección C): EVITACIÓN ABSOLUTA Y ESTRICTA.** Considera sinónimos y productos derivados del alérgeno listado (ej. si la alergia es 'maní', se debe evitar 'cacahuete', 'mantequilla de maní', etc.).
        *   ADAPTAR el plan meticulosamente a TODAS las patologías indicadas (Sección C).
        *   **CUMPLIMIENTO ESTRICTO DE LÍMITES DE MICRONUTRIENTES (Sección E) Y RESTRICCIONES CRÍTICAS (Sección H, si existe).**
        *   Respetar y EJEMPLIFICAR el Tipo de Dieta Base (Sección D) en todas las sugerencias.
        *   **Uso de Preparaciones Sugeridas (Sección F):** Integra las preparaciones sugeridas de forma natural y variada, manteniendo su esencia pero ajustando cantidades. NO bases todo el plan exclusivamente en ellas.
    6.  **Preferencias y Aversiones (Sección C):** INTENTAR incluir preferencias, EVITAR aversiones.
    7.  **Tono y Formato de Salida:**
        *   Utiliza un lenguaje técnico, claro y preciso, adecuado para un Licenciado en Nutrición.
        *   **SALIDA ESTRICTA: La respuesta debe consistir ÚNICA Y EXCLUSIVAMENTE en el plan semanal estructurado. NO incluyas NINGÚN comentario introductorio, notas explicativas, disculpas, advertencias, ni la sección "== RECETARIO DETALLADO ==". Solo el plan de comidas de Lunes a Domingo.**
    
    **Genera el BORRADOR de la ESTRUCTURA del plan nutricional semanal ahora, siguiendo TODAS estas instrucciones al pie de la letra. La PRIORIDAD NÚMERO UNO es alcanzar el GET (Sección B). Presta MÁXIMA ATENCIÓN a la Sección H (Restricciones Críticas por Patología) si está presente, ya que sus directrices anulan cualquier otra instrucción conflictiva. Luego, enfócate en el cumplimiento ESTRICTO de los objetivos de macronutrientes (Sección D o H, considerando la nota sobre actividad física), las restricciones de micronutrientes (Sección E), las consideraciones de textura (Sección G), la variedad de platos (incluyendo frutas y verduras diversas), y la eliminación total de cualquier texto que no sea el plan de comidas.**
    """
    return prompt

def generar_recetas_detalladas_prompt(lista_nombres_platos_con_numero, plan_input_data):
    """Prepara el prompt para generar las RECETAS DETALLADAS."""
    
    allergies_str = ', '.join(plan_input_data.get('allergies', [])) or 'Ninguna conocida'
    diet_type_str = plan_input_data.get('diet_type')
    if plan_input_data.get('other_diet_type_text'):
        diet_type_str = plan_input_data.get('other_diet_type_text')
    if not diet_type_str:
        diet_type_str = "General saludable"

    platos_para_prompt = ""
    for numero_receta, nombre_plato in lista_nombres_platos_con_numero:
        platos_para_prompt += f"{numero_receta}. {nombre_plato}\n"

    # Obtener una lista de nombres de ingredientes de la base de datos
    # Podrías hacer esto más selectivo si la lista es demasiado grande (ej. los más comunes, o por categoría)
    available_ingredients_from_db = []
    try:
        # Tomar solo los primeros N para no hacer el prompt demasiado largo, o filtrar por relevancia
        ingredients_db_objects = Ingredient.query.order_by(Ingredient.name).limit(300).all() # Limitar para no exceder el prompt
        available_ingredients_from_db = [ing.name for ing in ingredients_db_objects]
        app.logger.info(f"Se obtuvieron {len(available_ingredients_from_db)} nombres de ingredientes de la BD para el prompt de recetas.")
    except Exception as e:
        app.logger.error(f"Error al obtener ingredientes de la BD para el prompt: {e}")

    prompt = f"""
    Eres un asistente experto en nutrición clínica altamente capacitado. Tu tarea es generar las RECETAS DETALLADAS para una lista de platos, destinadas a un Licenciado en Nutrición.

    **CONTEXTO DEL PACIENTE (para guiar la creación de recetas):**
    *   **Tipo de Dieta Base OBLIGATORIA:** {diet_type_str}
    *   **Alergias a EVITAR ESTRICTAMENTE (incluyendo derivados y sinónimos):** {allergies_str}

    **LISTA DE INGREDIENTES DISPONIBLES (Prioriza su uso exacto o muy similar):**
    {', '.join(available_ingredients_from_db) if available_ingredients_from_db else "No hay lista específica, usa ingredientes comunes y apropiados."}
    **(Si un ingrediente de la lista es muy específico, como 'Arroz, grano, blanco, pulido, crudo', puedes usar una forma más común como 'Arroz blanco crudo' en la receta, pero intenta que el nombre base del alimento coincida con alguno de la lista).**

    **INSTRUCCIONES PARA LA GENERACIÓN DE RECETAS:**
    1.  **Genera una receta detallada para CADA UNO de los siguientes platos numerados.** La numeración de la receta en tu respuesta debe coincidir con la numeración proporcionada en la lista de platos.
    2.  **Formato Estándar OBLIGATORIO para CADA RECETA:**
        *   **Receta N°X: Título del Plato** (El título debe coincidir con el nombre del plato proporcionado)
        *   **Porciones que Rinde:** (Ej: "Rinde: 1 porción". Asegúrate que la receta esté calculada para 1 porción del plan nutricional).
        *   **Especificidad de Ingredientes:** Cuando un ingrediente tiene variantes comunes (ej. yogur natural, yogur descremado, yogur griego; leche entera, leche descremada), intenta especificar el tipo más adecuado para el contexto del plan o, en su defecto, usa el tipo más común (ej. "yogur natural entero", "leche entera"). Si usas un término genérico, el sistema asumirá un tipo estándar.
        *   **Ingredientes (para 1 porción):**
            *   Lista detallada de todos los ingredientes.
            *   Para CADA ingrediente, indica la cantidad en MEDIDAS CASERAS comunes Y su equivalente APROXIMADO en GRAMOS (g) o MILILITROS (ml).
            *   **Realismo en Cantidades:** Las cantidades de los ingredientes deben ser realistas y prácticas para una porción individual. Si un ingrediente comúnmente se presenta en una unidad mayor (ej. una pechuga de pollo entera), la receta debe especificar el uso de una fracción adecuada (ej. '1/2 pechuga de pollo mediana (aprox. 120g)') o si la receta está pensada para rendir más porciones que se usarán en el plan, indícalo claramente en "Porciones que Rinde" y ajusta los ingredientes para esa cantidad total.
        *   **Preparación:**
            *   Pasos numerados, claros, concisos y fáciles de seguir.
        *   **Condimentos Sugeridos:**
            *   Opciones adecuadas para el plato y compatibles con las restricciones (especialmente bajo sodio si aplica).
        *   **Sugerencia de Presentación/Servicio:**
            *   Breve idea para servir el plato.
    3.  **Calidad Culinaria:** Las recetas deben ser apetecibles, realistas, y utilizar métodos de cocción y combinaciones de sabores propias del Tipo de Dieta Base.
    4.  **Evitación de Alergias:** Las recetas NO DEBEN CONTENER NINGUNO de los alérgenos listados, ni sus derivados o sinónimos.
    5.  **Tono y Formato de Salida:**
        *   Utiliza un lenguaje técnico, claro y preciso.
        *   **SALIDA ESTRICTA: La respuesta debe comenzar DIRECTAMENTE con "== RECETARIO DETALLADO ==" seguido de todas las recetas numeradas. NO incluyas NINGÚN comentario introductorio, notas explicativas, disculpas, advertencias, ni ningún otro texto que no sea parte integral del recetario mismo.**
    6.  **Asegúrate de que cada receta sea completa y no se corte.**
    7.  **"Evita introducciones o conclusiones largas en la sección de recetas; ve directo al detalle de cada una**
    
    **LISTA DE PLATOS PARA DESARROLLAR RECETAS:**
{platos_para_prompt}

    **Genera el RECETARIO DETALLADO ahora, siguiendo TODAS estas instrucciones al pie de la letra. Asegúrate de generar una receta para CADA plato listado y de eliminar cualquier texto superfluo.**
    """
    return prompt

def extraer_nombres_de_recetas(texto_plan_estructura):
    """Extrae los nombres de los platos y sus números de receta del plan estructurado."""
    patron = re.compile(r"([A-Za-z0-9ÁÉÍÓÚáéíóúñÑ\s,.'-]+?)\s*\(Ver Receta N°(\d+)\)")
    matches = patron.findall(texto_plan_estructura)    
    recetas_extraidas = []
    for nombre, numero in matches:
        recetas_extraidas.append((f"N°{numero}", nombre.strip()))

    recetas_unicas_por_numero = {}
    for numero_receta_str, nombre_plato in recetas_extraidas:
        if numero_receta_str not in recetas_unicas_por_numero:
            recetas_unicas_por_numero[numero_receta_str] = nombre_plato
    
    lista_final_recetas = list(recetas_unicas_por_numero.items())
    app.logger.info(f"Recetas extraídas para desarrollo: {lista_final_recetas}")
    return lista_final_recetas

def generar_plan_nutricional_v2(plan_input_data):
    """Genera plan nutricional completo en dos pasos: estructura y luego recetas."""
    if not app.config['GEMINI_API_KEY']:
        return "Error: API Key de Gemini no configurada."

    model_name = 'gemini-1.5-pro-latest'
    try:
        model = genai.GenerativeModel(model_name)
    except Exception as model_error:
        app.logger.error(f"ERROR: Creando modelo Gemini: {model_error}")
        return "Error: Inicializando modelo IA."

    # --- PASO 1: Generar la ESTRUCTURA del Plan ---
    prompt_estructura = generar_estructura_plan_prompt(plan_input_data)
    app.logger.info("Enviando prompt para ESTRUCTURA del plan a Gemini.")
    
    safety_settings=[{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}, {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}, {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}, {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"}]
    generation_config = genai.types.GenerationConfig(
        temperature=0.7, 
        top_p=0.9,       
        top_k=40,        
        max_output_tokens=8000 # Aumentado para permitir planes más largos
    )

    texto_plan_estructura = ""
    try:
        response_estructura = model.generate_content(
            prompt_estructura, 
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        app.logger.info("Respuesta de ESTRUCTURA recibida de Gemini.")
        
        if not response_estructura.parts:
            block_reason_attr = getattr(response_estructura, 'prompt_feedback', None)
            reason_value = getattr(block_reason_attr, 'block_reason', "Razón desconocida")
            reason_message = getattr(block_reason_attr, 'block_reason_message', "")
            
            partial_text = ""
            if hasattr(response_estructura, 'candidates') and response_estructura.candidates:
                candidate = response_estructura.candidates[0]
                if hasattr(candidate, 'content') and candidate.content.parts:
                    partial_text = candidate.content.parts[0].text
            
            if partial_text:
                app.logger.warning(f"Respuesta de ESTRUCTURA Gemini bloqueada (Razón: {reason_value} - {reason_message}). Usando texto parcial: {partial_text[:100]}...")
                texto_plan_estructura = partial_text
            else:
                app.logger.error(f"Respuesta de ESTRUCTURA Gemini bloqueada o vacía. Razón: {reason_value} - {reason_message}")
                return f"Error: La IA bloqueó la respuesta para la estructura del plan (Razón: {reason_value} - {reason_message})."
        else:
            texto_plan_estructura = response_estructura.text
            app.logger.info("Texto de ESTRUCTURA del plan obtenido.")

    except Exception as e:
        app.logger.error(f"Error llamando a Gemini para ESTRUCTURA: {e}", exc_info=True)
        return "Error inesperado al generar estructura del plan con IA."

    # --- PASO 2: Extraer nombres de recetas y generar RECETAS DETALLADAS ---
    lista_platos_para_recetas = extraer_nombres_de_recetas(texto_plan_estructura)

    if not lista_platos_para_recetas:
        app.logger.warning("No se encontraron referencias a recetas (Ver Receta N°X) en la estructura del plan. No se generarán recetas detalladas.")
        # Aún así, proceder a generar la sección de recetas, pero con una lista vacía,
        # para que el marcador "== RECETARIO DETALLADO ==" se incluya.
        prompt_recetas = generar_recetas_detalladas_prompt([], plan_input_data) 
    else:
        prompt_recetas = generar_recetas_detalladas_prompt(lista_platos_para_recetas, plan_input_data)
    app.logger.info(f"Enviando prompt para RECETAS DETALLADAS ({len(lista_platos_para_recetas)} recetas) a Gemini.")
    
    texto_recetario_detallado = ""
    try:
        response_recetas = model.generate_content(
            prompt_recetas, 
            generation_config=generation_config, # Reutilizar config o ajustar si es necesario
            safety_settings=safety_settings
        )
        app.logger.info("Respuesta de RECETAS DETALLADAS recibida de Gemini.")

        if not response_recetas.parts:
            block_reason_attr = getattr(response_recetas, 'prompt_feedback', None)
            reason_value = getattr(block_reason_attr, 'block_reason', "Razón desconocida")
            reason_message = getattr(block_reason_attr, 'block_reason_message', "")
            
            partial_text_recetas = ""
            if hasattr(response_recetas, 'candidates') and response_recetas.candidates:
                candidate = response_recetas.candidates[0]
                if hasattr(candidate, 'content') and candidate.content.parts:
                    partial_text_recetas = candidate.content.parts[0].text

            if partial_text_recetas:
                 app.logger.warning(f"Respuesta de RECETAS Gemini bloqueada (Razón: {reason_value} - {reason_message}). Usando texto parcial de recetas: {partial_text_recetas[:100]}...")
                 texto_recetario_detallado = partial_text_recetas
            else:
                app.logger.error(f"Respuesta de RECETAS Gemini bloqueada o vacía. Razón: {reason_value} - {reason_message}. No se generaron recetas detalladas.")
                texto_recetario_detallado = f"\n\n---\nError: La IA bloqueó la generación de recetas detalladas (Razón: {reason_value} - {reason_message}). Por favor, desarróllalas manualmente."
        else:
            texto_recetario_detallado = response_recetas.text
            app.logger.info("Texto de RECETARIO DETALLADO obtenido.")

    except Exception as e:
        app.logger.error(f"Error inesperado llamando a Gemini para RECETAS: {e}", exc_info=True)
        texto_recetario_detallado = "\n\n---\nError inesperado al generar recetas detalladas con IA. Por favor, desarróllalas manualmente."

    # --- PASO 3: Combinar estructura y recetas ---
    plan_completo = texto_plan_estructura + "\n\n" + texto_recetario_detallado
    app.logger.info("Plan completo (estructura + recetas) ensamblado.")
    return plan_completo
# ... (aquí termina tu función generar_plan_nutricional_v2)
#     app.logger.info("Plan completo (estructura + recetas) ensamblado.")
#     return plan_completo

# --- NUEVA FUNCIÓN AUXILIAR: Parsear todas las recetas de un bloque de texto ---
# LA SIGUIENTE LÍNEA "def" DEBE ESTAR EN LA PRIMERA COLUMNA, SIN INDENTACIÓN:
def parse_all_recipes_from_text_block(recetario_text):
    """
    Parses all recipes from the '== RECETARIO DETALLADO ==' text block.
    Returns a list of parsed recipe dictionaries.
    """
    app.logger.debug(f"--- Iniciando parse_all_recipes_from_text_block ---")
    if not recetario_text or recetario_text.strip() == "No se pudieron parsear las recetas detalladas." or "Error:" in recetario_text :
        app.logger.warning("Texto del recetario vacío o con error previo. No se parsearán recetas.")
        return []

    app.logger.debug(f"Texto del recetario recibido (primeros 1000 chars):\n{recetario_text[:1000]}")
    recipes = []

    # Eliminar el marcador inicial si está presente para evitar que interfiera con el primer split
    clean_recetario_text = re.sub(r"^\s*== RECETARIO DETALLADO ==\s*\n?", "", recetario_text, flags=re.IGNORECASE).strip()
    if not clean_recetario_text:
        app.logger.warning("Texto del recetario vacío después de quitar el marcador inicial.")
        return []

    # Dividir el texto en bloques de recetas individuales.
    # El lookahead positivo (?=...) asegura que el delimitador "Receta N°X:" se mantenga al inicio de cada bloque subsiguiente.
    recipe_blocks_raw = re.split(r"\n(?=\s*\**\s*Receta\s*(?:N°|No\.|N\.)?\s*\d+:)", clean_recetario_text, flags=re.IGNORECASE)
    app.logger.debug(f"Bloques crudos después del split inicial: {len(recipe_blocks_raw)}")

    for i, block_text_raw in enumerate(recipe_blocks_raw):
        block_text = block_text_raw.strip()
        if not block_text:
            continue

        app.logger.debug(f"Procesando bloque crudo {i}: '{block_text[:200]}...'")

        # Patrón para capturar el número de receta y el nombre.
        # Es más robusto si el nombre puede contener saltos de línea antes de las secciones.
        # Modificado para manejar ** y detenerse antes de "Rinde:" o "Porciones que Rinde:"
        # Intento 1: Título en una línea, seguido de secciones clave en nuevas líneas.
        title_match_attempt1 = re.match(
            r"\**\s*(Receta\s*(?:N°|No\.|N\.)?\s*\d+):\s*([^\n]+?)\s*\**\s*(?=\n\s*(?:Porciones que Rinde:|Rinde:|Ingredientes:|Preparación:)|$)",
            block_text,
            re.IGNORECASE | re.DOTALL
        )
        title_match = title_match_attempt1
 #
        if not title_match:
            # Intento 2: Título más simple que solo toma la primera línea que empieza con "Receta..."
            title_match_attempt2 = re.match(
                r"\**\s*(Receta\s*(?:N°|No\.|N\.)?\s*\d+):\s*([^\n]+)",
                block_text,
                re.IGNORECASE
            )
            title_match = title_match_attempt2
            if not title_match:
                app.logger.warning(f"No se pudo parsear el título para el bloque (ambos intentos fallaron): '{block_text[:250]}...'")
                continue
        
        recipe_number_full = title_match.group(1).strip()
        recipe_name = title_match.group(2).strip().replace('\n', ' ') # Unir nombre si tiene saltos de línea
        app.logger.debug(f"  Título parseado: {recipe_number_full} - {recipe_name}")

        content_after_title = block_text[title_match.end():].strip()
        app.logger.debug(f"  Contenido después del título (primeros 100 chars): '{content_after_title[:100]}...'")

        ingredients_list_for_pdf = []
        instructions_text = ""
        servings_text = ""
        condiments_text = ""
        presentation_text = ""

        servings_match = re.search(r"Porciones que Rinde:\s*(.*?)(?=\n\s*(?:Ingredientes:|Preparación:)|$)", content_after_title, re.DOTALL | re.IGNORECASE)
        if servings_match:
            servings_text = servings_match.group(1).strip()
            app.logger.debug(f"    Porciones: {servings_text}")

        # Regex to capture ingredients. Stops at known section headers OR a line that looks like a sub-recipe title (e.g., "Mayonesa:")
        # OR a numbered list item (likely start of preparation steps if "Preparación:" was missed) OR end of block.
        # Lookahead for sub-header: newline, optional spaces, then text ending with colon.
        ingredients_section_regex = r"Ingredientes\s*(?:\([^)]*\))?:\s*\n((?:.|\n)*?)(?=\n\s*(?:Preparación:|Condimentos Sugeridos:|Sugerencia de Presentación:|[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚáéíóúñÑ\s(),'-]*:|\d+\.\s)|$)"
        
        ingredients_section_match = re.search(ingredients_section_regex, content_after_title, re.DOTALL | re.IGNORECASE)
        if ingredients_section_match:
            ingredients_block_text = ingredients_section_match.group(1).strip()
            app.logger.debug(f"    Bloque de Ingredientes detectado:\n{ingredients_block_text[:250]}...")
            current_ingredients = []
            for line in ingredients_block_text.split('\n'):
                line = line.strip()
                if line.startswith(('*', '-', '•')):
                    ingredient_text_cleaned = line.lstrip('*-• ').strip()
                    if ingredient_text_cleaned:
                        # Aquí no llamamos a _parse_ingredient_line, solo guardamos la línea cruda para el PDF
                        current_ingredients.append({'raw_line': ingredient_text_cleaned})
                        app.logger.debug(f"      Ingrediente añadido (raw): '{ingredient_text_cleaned}'")
                elif line: # Considerar líneas que no son viñetas como parte del ingrediente anterior si es multilínea
                    # Only append if it's a continuation, not a new sub-header or prep step.
                    is_sub_header_candidate = re.match(r"^\s*[A-Za-zÁÉÍÓÚáéíóúñÑ][A-Za-zÁÉÍÓÚáéíóúñÑ\s(),'-]*:", line)
                    is_prep_step_candidate = re.match(r"^\s*\d+\.\s+", line)

                    if current_ingredients and 'raw_line' in current_ingredients[-1] and \
                       not is_sub_header_candidate and not is_prep_step_candidate:
                        current_ingredients[-1]['raw_line'] += " " + line
                        app.logger.debug(f"      Línea continuada de ingrediente: '{line}' añadida a '{current_ingredients[-1]['raw_line']}'")
                    else: 
                        app.logger.debug(f"      Línea en ingredientes NO reconocida como viñeta o continuación válida: '{line}' (SubHeader: {bool(is_sub_header_candidate)}, PrepStep: {bool(is_prep_step_candidate)})")
            ingredients_list_for_pdf = current_ingredients
        else:
            app.logger.warning(f"    No se encontró la sección 'Ingredientes:' para '{recipe_name}'")

        prep_match = re.search(r"Preparación:\s*\n(.*?)(?=\n\s*(?:Condimentos Sugeridos:|Sugerencia de Presentación:)|$)", content_after_title, re.DOTALL | re.IGNORECASE)
        if prep_match:
            instructions_text = prep_match.group(1).strip()
            app.logger.debug(f"    Preparación (primeros 100 chars): {instructions_text[:100]}...")
        else:
            app.logger.warning(f"    No se encontró la sección 'Preparación:' para '{recipe_name}'")

        condiments_match = re.search(r"Condimentos Sugeridos:\s*(.*?)(?=\n\s*(?:Sugerencia de Presentación:)|$)", content_after_title, re.DOTALL | re.IGNORECASE)
        if condiments_match:
            condiments_text = condiments_match.group(1).strip()
            app.logger.debug(f"    Condimentos: {condiments_text[:100]}...")

        presentation_match = re.search(r"Sugerencia de Presentación(?:/Servicio)?:\s*(.*?)(?=\n\nReceta\s*(?:N°|No\.|N\.)?\s*\d+:|$)", content_after_title, re.DOTALL | re.IGNORECASE)
        if presentation_match:
            presentation_text = presentation_match.group(1).strip()
            app.logger.debug(f"    Presentación: {presentation_text[:100]}...")

        if recipe_name: # Una receta es válida si al menos tiene un nombre
            recipes.append({
                'number': recipe_number_full,
                'name': recipe_name,
                'servings': servings_text,
                'ingredients': ingredients_list_for_pdf, # Lista de dicts {'raw_line': '...'}
                'instructions': instructions_text,
                'condiments': condiments_text,
                'presentation': presentation_text
            })
            app.logger.info(f"Receta '{recipe_name}' parseada y añadida.")
        else:
            app.logger.warning(f"Receta omitida por falta de nombre. Bloque: '{block_text[:100]}...'")

    app.logger.debug(f"--- parse_all_recipes_from_text_block finalizado. Recetas parseadas: {len(recipes)} ---")
    return recipes



# La siguiente función debería ser def crear_pdf_v2(...):
# ... (resto de tu código) ...



# (Función crear_pdf_v2 - CORREGIDA)
def crear_pdf_v2(evaluation_instance):
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    x_margin = 50
    y_margin = 45
    line_height = 13 
    max_width = width - (2 * x_margin)
    current_y = height - y_margin
    
    app.logger.info(f"PDF_DEBUG (crear_pdf_v2): Iniciando generación para Evaluación ID: {evaluation_instance.id}")

    risk_colors = {'bajo': colors.Color(0.2, 0.6, 0.3), 'medio': colors.Color(0.9, 0.7, 0.1), 'alto': colors.Color(0.75, 0.25, 0.25), None: colors.gray}
    def draw_risk_indicator(x, y, risk_level):
        radius = 3.5; color = risk_colors.get(risk_level, colors.gray)
        p.setFillColor(color); p.circle(x, y + radius/1.5, radius, fill=1, stroke=0); p.setFillColor(colors.black)

    try:
        patient = evaluation_instance.patient 
        if not patient: raise ValueError("Evaluación no asociada a un paciente.")

        p.setFont("Helvetica-Bold", 15)
        p.drawString(x_margin, current_y, f"Evaluación Nutricional - {patient.name} {patient.surname}")
        current_y -= line_height * 1.5
        p.setFont("Helvetica", 10)
        p.drawString(x_margin, current_y, f"Fecha Evaluación: {evaluation_instance.consultation_date.strftime('%d/%m/%Y %H:%M')} UTC")
        current_y -= line_height * 2.5

        _line_height_for_sections = 13
        
        def draw_section_title(title_text):
            nonlocal current_y
            if current_y < y_margin + _line_height_for_sections * 4:
                p.showPage(); current_y = height - y_margin
            p.setFont("Helvetica-Bold", 12)
            p.drawString(x_margin, current_y, title_text)
            current_y -= _line_height_for_sections * 0.6
            p.line(x_margin, current_y, width - x_margin, current_y)
            current_y -= _line_height_for_sections * 1.5
            p.setFont("Helvetica", 9.5)

        def draw_line_simple(text, indent_val=10):
            nonlocal current_y
            lines = simpleSplit(text, p._fontname, p._fontsize, max_width - indent_val)
            for line_seg in lines:
                if current_y < y_margin + _line_height_for_sections:
                    p.showPage(); p.setFont(p._fontname, p._fontsize); current_y = height - y_margin
                p.drawString(x_margin + indent_val, current_y, line_seg.strip())
                current_y -= _line_height_for_sections * 1.1
        
        draw_section_title("I. Datos del Paciente")
        draw_line_simple(f"Nombre: {patient.name} {patient.surname} (C.I.: {patient.cedula})")
        draw_line_simple(f"F. Nac: {patient.dob.strftime('%d/%m/%Y') if patient.dob else 'N/A'} | Edad: {patient.calculate_age() or 'N/A'} | Sexo: {patient.sex or 'N/A'}")
        draw_line_simple(f"Contacto: {patient.email or 'N/A'} / {patient.phone_number or 'N/A'}")
        draw_line_simple(f"Educación: {patient.education_level or 'N/A'} | P. Adquisitivo: {patient.purchasing_power or 'N/A'}")
        if evaluation_instance.gestational_age_weeks and evaluation_instance.gestational_age_weeks > 0:
             draw_line_simple(f"Edad Gestacional: {evaluation_instance.gestational_age_weeks} semanas")
        draw_line_simple(f"Alergias: {', '.join(patient.get_allergies()) or 'Ninguna'}")
        draw_line_simple(f"Intolerancias: {', '.join(patient.get_intolerances()) or 'Ninguna'}")
        draw_line_simple(f"Preferencias: {', '.join(patient.get_preferences()) or 'Ninguna'}")
        draw_line_simple(f"Aversiones: {', '.join(patient.get_aversions()) or 'Ninguna'}")
        current_y -= _line_height_for_sections * 0.8

        draw_section_title("II. Evaluación Actual")
        draw_line_simple(f"Peso: {evaluation_instance.weight_at_eval or 'N/A'} kg | Altura: {patient.height_cm or 'N/A'} cm | Complexión: {evaluation_instance.calculated_complexion or 'N/A'}")
        draw_line_simple(f"P. Muñeca: {evaluation_instance.wrist_circumference_cm or 'N/A'} cm | P. Cintura: {evaluation_instance.waist_circumference_cm or 'N/A'} cm | P. Cadera: {evaluation_instance.hip_circumference_cm or 'N/A'} cm")
        draw_line_simple(f"Peso Ideal (Est.): {evaluation_instance.calculated_ideal_weight or 'N/A'} kg | GET (Est.): {evaluation_instance.calculated_calories or 'N/A'} kcal (Factor Act: {evaluation_instance.activity_factor or 'N/A'})")
        
        imc_text = f"IMC: {evaluation_instance.calculated_imc or 'N/A'}"
        whr_text = f"ICC: {evaluation_instance.calculated_waist_hip_ratio or 'N/A'}"
        whtr_text = f"ICA: {evaluation_instance.calculated_waist_height_ratio or 'N/A'}"
        
        y_before_imc = current_y; draw_line_simple(imc_text)
        if evaluation_instance.imc_risk: draw_risk_indicator(x_margin + 10 + p.stringWidth(imc_text, p._fontname, p._fontsize) + 8, y_before_imc - (_line_height_for_sections*1.1)/2 + 1.5 , evaluation_instance.imc_risk)
        y_before_whr = current_y; draw_line_simple(whr_text)
        if evaluation_instance.whr_risk: draw_risk_indicator(x_margin + 10 + p.stringWidth(whr_text, p._fontname, p._fontsize) + 8, y_before_whr - (_line_height_for_sections*1.1)/2 + 1.5, evaluation_instance.whr_risk)
        y_before_whtr = current_y; draw_line_simple(whtr_text)
        if evaluation_instance.whtr_risk: draw_risk_indicator(x_margin + 10 + p.stringWidth(whtr_text, p._fontname, p._fontsize) + 8, y_before_whtr - (_line_height_for_sections*1.1)/2 + 1.5, evaluation_instance.whtr_risk)
        current_y -= _line_height_for_sections * 0.8

        draw_section_title("III. Condiciones Clínicas y Dieta del Plan")
        draw_line_simple(f"Patologías (Evaluación): {', '.join(evaluation_instance.get_pathologies()) or 'Ninguna'}")
        if evaluation_instance.other_pathologies_text: draw_line_simple(f"  Otras Patologías: {evaluation_instance.other_pathologies_text}")
        if evaluation_instance.postoperative_text: draw_line_simple(f"Postoperatorio: {evaluation_instance.postoperative_text}")
        diet_type_pdf = evaluation_instance.diet_type or 'No especificada'
        if evaluation_instance.other_diet_type_text: diet_type_pdf += f" ({evaluation_instance.other_diet_type_text})"
        draw_line_simple(f"Tipo de Dieta Base: {diet_type_pdf}")
        macros_obj_pdf = f"P: {evaluation_instance.target_protein_perc or 'N/A'}% | C: {evaluation_instance.target_carb_perc or 'N/A'}% | G: {evaluation_instance.target_fat_perc or 'N/A'}%"
        draw_line_simple(f"Objetivo Peso: {evaluation_instance.target_weight or 'N/A'} kg | Obj. Cintura: {evaluation_instance.target_waist_cm or 'N/A'} cm")
        draw_line_simple(f"Obj. Macros: {macros_obj_pdf}")
        current_y -= _line_height_for_sections * 0.8

        draw_section_title("IV. Plan Semanal Sugerido")
        full_plan_text = evaluation_instance.edited_plan_text or "Plan no disponible."
        app.logger.debug(f"PDF_DEBUG (crear_pdf_v2): full_plan_text (primeros 300): '{full_plan_text[:300]}'")
        recetario_marker = "== RECETARIO DETALLADO =="
        plan_parts = full_plan_text.split(recetario_marker, 1)
        plan_structure_text = plan_parts[0].strip()
        recetario_block_text = plan_parts[1].strip() if len(plan_parts) > 1 else ""
        app.logger.debug(f"PDF_DEBUG (crear_pdf_v2): plan_structure_text (primeros 300): '{plan_structure_text[:300]}'")
        app.logger.debug(f"PDF_DEBUG (crear_pdf_v2): recetario_block_text (primeros 300): '{recetario_block_text[:300]}'")

        if plan_structure_text:
            day_regex_tech = re.compile(r"^\s*\*\*(Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Domingo)\s*\*\*(?::)?", re.IGNORECASE)
            meal_regex_tech = re.compile(r"^\s*\*?\s*(Desayuno|Colación Mañana|Almuerzo|Colación Tarde|Cena|Merienda)\s*:\s*", re.IGNORECASE)
            plan_lines_tech = plan_structure_text.split('\n')
            line_height_tech_ref = 11

            for i_tech, line_raw_tech in enumerate(plan_lines_tech):
                line_tech = line_raw_tech.strip()
                if not line_tech:
                    current_y -= line_height_tech_ref * 0.5 
                    continue
                is_day_title_tech = day_regex_tech.match(line_tech)
                is_meal_title_tech = meal_regex_tech.match(line_tech)
                font_name_tech = "Helvetica"; font_size_tech = 9.0; indent_tech = 0
                space_before_tech = 0; line_spacing_mult_tech = 1.2
                if is_day_title_tech:
                    font_name_tech = "Helvetica-Bold"; font_size_tech = 10.0
                    if i_tech > 0: space_before_tech = line_height_tech_ref * 0.9
                    line_tech = day_regex_tech.sub(r"\1:", line_tech).upper()
                    app.logger.debug(f"PDF_DEBUG (crear_pdf_v2): Plan - Día: '{line_tech}'")
                elif is_meal_title_tech:
                    font_name_tech = "Helvetica-Bold"; font_size_tech = 9.0; indent_tech = 5
                    space_before_tech = line_height_tech_ref * 0.4
                    line_tech = meal_regex_tech.sub(r"\1:", line_tech)
                    app.logger.debug(f"PDF_DEBUG (crear_pdf_v2): Plan - Comida: '{line_tech}'")
                else:
                    indent_tech = 10; cleaned_line_tech = line_tech.lstrip("*-• ").strip()
                    line_tech = f"• {cleaned_line_tech}" if cleaned_line_tech else ""
                    line_spacing_mult_tech = 1.15
                    # app.logger.debug(f"PDF_DEBUG (crear_pdf_v2): Plan - Item: '{line_tech}'") # Puede ser muy verboso
                current_y -= space_before_tech
                current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_tech_ref, line_tech, font_name_tech, font_size_tech, indent=indent_tech, line_spacing_factor=line_spacing_mult_tech, y_margin_page=y_margin, page_height_ref=height)
            current_y -= line_height_tech_ref * 1.0
        else:
             draw_line_simple("Plan no disponible.")
             current_y -= _line_height_for_sections * 1.0

        if evaluation_instance.user_observations:
            draw_section_title("V. Observaciones Adicionales")
            p.setFont("Helvetica", 9.5) # Asegurar que la fuente se establece antes de llamar a draw_text_block_with_style
            current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, _line_height_for_sections, evaluation_instance.user_observations, "Helvetica", 9.5, indent=0, line_spacing_factor=1.1)

        if recetario_block_text:
            p.showPage(); current_y = height - y_margin
            draw_section_title("VI. Recetario Detallado")
            line_height_recipe_tech = 10.5
            parsed_recipes_tech = parse_all_recipes_from_text_block(recetario_block_text)
            app.logger.info(f"PDF_DEBUG (crear_pdf_v2): Recetas parseadas para PDF técnico: {len(parsed_recipes_tech)}")

            if parsed_recipes_tech:
                for i_recipe, recipe_tech in enumerate(parsed_recipes_tech):
                    app.logger.debug(f"PDF_DEBUG (crear_pdf_v2): Dibujando receta técnica N°{i_recipe+1}: {recipe_tech.get('name', 'Sin Nombre')}")
                    if current_y < y_margin + _line_height_for_sections * 6: 
                        p.showPage(); current_y = height - y_margin; draw_section_title("VI. Recetario Detallado (Cont.)")
                    
                    title_tech_str = f"{recipe_tech.get('number', 'Receta N/N')}: {recipe_tech.get('name', 'Sin Nombre')}"
                    current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_recipe_tech, title_tech_str, "Helvetica-Bold", 10.0, indent=0, line_spacing_factor=1.2)
                    current_y -= line_height_recipe_tech * 0.3

                    if recipe_tech.get('servings'):
                        current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_recipe_tech, f"Rinde: {recipe_tech['servings']}", "Helvetica-Oblique", 8.5, indent=10, line_spacing_factor=1.15)
                        current_y -= line_height_recipe_tech * 0.3
                    if recipe_tech.get('ingredients'):
                        current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_recipe_tech, "Ingredientes:", "Helvetica-Bold", 9.0, indent=10, line_spacing_factor=1.2)
                        for ing_tech in recipe_tech['ingredients']:
                            ing_text_tech = ing_tech.get('raw_line', 'Ingrediente desconocido')
                            current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_recipe_tech, ing_text_tech, "Helvetica", 8.5, indent=20, is_list_item=True, bullet="• ", line_spacing_factor=1.1)
                        current_y -= line_height_recipe_tech * 0.3
                    if recipe_tech.get('instructions'):
                        current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_recipe_tech, "Preparación:", "Helvetica-Bold", 9.0, indent=10, line_spacing_factor=1.2)
                        instruction_lines_tech = str(recipe_tech['instructions']).split('\n') # Asegurar que sea string
                        for instr_line_tech in instruction_lines_tech:
                            is_numbered_step = re.match(r"^\s*\d+\.\s+", instr_line_tech.strip())
                            instr_indent = 20
                            current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_recipe_tech, instr_line_tech.strip(), "Helvetica", 8.5, indent=instr_indent, line_spacing_factor=1.1)
                        current_y -= line_height_recipe_tech * 0.3
                    if recipe_tech.get('condiments'):
                        current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_recipe_tech, "Condimentos Sugeridos:", "Helvetica-Bold", 9.0, indent=10, line_spacing_factor=1.2)
                        current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_recipe_tech, recipe_tech['condiments'], "Helvetica", 8.5, indent=20, line_spacing_factor=1.1)
                        current_y -= line_height_recipe_tech * 0.3
                    if recipe_tech.get('presentation'):
                        current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_recipe_tech, "Sugerencia de Presentación/Servicio:", "Helvetica-Bold", 9.0, indent=10, line_spacing_factor=1.2)
                        current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_recipe_tech, recipe_tech['presentation'], "Helvetica", 8.5, indent=20, line_spacing_factor=1.1)
                    current_y -= line_height_recipe_tech * 0.8
            else:
                p.setFont("Helvetica-Oblique", 9)
                p.drawString(x_margin, current_y, "No se pudieron parsear las recetas detalladas o no hay recetas.")
                current_y -= line_height_recipe_tech * 1.1
        
        p.save()
        buffer.seek(0)
        app.logger.info(f"PDF_DEBUG (crear_pdf_v2): PDF (v2) generado exitosamente para Evaluación ID: {evaluation_instance.id}")
        return buffer
    except Exception as pdf_error:
        app.logger.error(f"Error creando PDF (v2) para Evaluación ID {evaluation_instance.id if evaluation_instance else 'N/A'}): {pdf_error}", exc_info=True)
        return io.BytesIO()

def draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_ref, text_content, font_name, font_size, indent=0, line_spacing_factor=1.1, y_margin_page=50, page_height_ref=letter[1], is_list_item=False, bullet="• "):
    """
    Helper function to draw a block of text with specific styling, handling line wrapping and page breaks.
    Returns the new current_y position.
    """
    p.setFont(font_name, font_size)
    # Asegurarse de que text_content sea un string
    text_content_str = str(text_content) if text_content is not None else ""

    lines = simpleSplit(text_content_str, p._fontname, p._fontsize, max_width - indent)
    new_y = current_y
    for i, line_text in enumerate(lines):
        if new_y < y_margin_page + line_height_ref: # Check if new page is needed
            p.showPage()
            new_y = page_height_ref - y_margin_page
            p.setFont(font_name, font_size) # Re-apply font on new page
        
        prefix = bullet if is_list_item and i == 0 else "" # Add bullet only to the first line of a list item
        p.drawString(x_margin + indent, new_y, prefix + line_text.strip())
        new_y -= line_height_ref * line_spacing_factor
    return new_y

# --- NUEVA FUNCIÓN: PDF para el Paciente ---
def crear_pdf_paciente(evaluation_instance):
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    x_margin = 50
    y_margin = 50
    line_height_base = 15 
    max_width = width - 2 * x_margin
    current_y = height - y_margin

    app.logger.info(f"PDF_DEBUG (crear_pdf_paciente): Iniciando generación para Evaluación ID: {evaluation_instance.id}")

    try:
        patient = evaluation_instance.patient
        if not patient:
            raise ValueError("Evaluación sin paciente asociado para PDF de paciente.")

        p.setFont("Helvetica-Bold", 18)
        p.drawCentredString(width / 2.0, current_y, f"Plan Nutricional para {patient.name} {patient.surname}")
        current_y -= line_height_base * 1.5
        p.setFont("Helvetica", 12)
        p.drawCentredString(width / 2.0, current_y, f"Fecha de Consulta: {evaluation_instance.consultation_date.strftime('%d/%m/%Y')}")
        current_y -= line_height_base * 3

        def draw_patient_section_title(title):
            nonlocal current_y
            if current_y < y_margin + line_height_base * 4: p.showPage(); current_y = height - y_margin
            p.setFont("Helvetica-Bold", 15)
            p.setFillColor(colors.HexColor("#003366"))
            p.drawString(x_margin, current_y, title)
            current_y -= line_height_base * 1.8
            p.setFont("Helvetica", 10.5)
            p.setFillColor(colors.black)
        
        draw_patient_section_title("Mi Plan de Alimentación Semanal")
        full_plan_text = evaluation_instance.edited_plan_text or "El plan de alimentación se detallará durante la consulta."
        app.logger.debug(f"PDF_DEBUG (crear_pdf_paciente): full_plan_text (primeros 300): '{full_plan_text[:300]}'")
        recetario_marker = "== RECETARIO DETALLADO =="
        plan_parts = full_plan_text.split(recetario_marker, 1)
        plan_structure_text = plan_parts[0].strip()
        recetario_block_text = plan_parts[1].strip() if len(plan_parts) > 1 else ""
        app.logger.debug(f"PDF_DEBUG (crear_pdf_paciente): plan_structure_text (primeros 300): '{plan_structure_text[:300]}'")
        app.logger.debug(f"PDF_DEBUG (crear_pdf_paciente): recetario_block_text (primeros 300): '{recetario_block_text[:300]}'")
        
        if plan_structure_text:
            day_regex_patient = re.compile(r"^\s*\*\*(Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Domingo)\s*\*\*(?::)?", re.IGNORECASE)
            meal_regex_patient = re.compile(r"^\s*\*?\s*(Desayuno|Colación Mañana|Almuerzo|Colación Tarde|Cena|Merienda)\s*:\s*", re.IGNORECASE)
            plan_lines_patient = plan_structure_text.split('\n')

            for i_patient, line_raw_patient in enumerate(plan_lines_patient):
                line_patient = line_raw_patient.strip()
                if not line_patient:
                    current_y -= line_height_base * 0.5
                    continue
                is_day_title_patient = day_regex_patient.match(line_patient)
                is_meal_title_patient = meal_regex_patient.match(line_patient)
                font_name_patient = "Helvetica"; font_size_patient = 10.5; indent_patient = 0
                space_before_patient = 0; line_spacing_mult_patient = 1.2
                if is_day_title_patient:
                    font_name_patient = "Helvetica-Bold"; font_size_patient = 12
                    if i_patient > 0: space_before_patient = line_height_base * 0.8
                    line_patient = day_regex_patient.sub(r"\1:", line_patient).upper()
                    app.logger.debug(f"PDF_DEBUG (crear_pdf_paciente): Plan - Día: '{line_patient}'")
                elif is_meal_title_patient:
                    font_name_patient = "Helvetica-Bold"; font_size_patient = 11; indent_patient = 10
                    space_before_patient = line_height_base * 0.3
                    line_patient = meal_regex_patient.sub(r"\1:", line_patient)
                    app.logger.debug(f"PDF_DEBUG (crear_pdf_paciente): Plan - Comida: '{line_patient}'")
                else:
                    indent_patient = 20; cleaned_line_patient = line_patient.lstrip("*-• ").strip()
                    line_patient = f"• {cleaned_line_patient}" if cleaned_line_patient else ""
                    line_spacing_mult_patient = 1.15
                    # app.logger.debug(f"PDF_DEBUG (crear_pdf_paciente): Plan - Item: '{line_patient}'") # Puede ser muy verboso
                current_y -= space_before_patient
                current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, line_patient, font_name_patient, font_size_patient, indent=indent_patient, line_spacing_factor=line_spacing_mult_patient, y_margin_page=y_margin, page_height_ref=height)
        else:
            current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, "Plan de comidas no disponible.", "Helvetica-Italic", 10)
        
        current_y -= line_height_base 

        parsed_recipes = parse_all_recipes_from_text_block(recetario_block_text)
        app.logger.info(f"PDF_DEBUG (crear_pdf_paciente): Recetas parseadas para PDF paciente: {len(parsed_recipes)}")

        if parsed_recipes:
            estimated_plan_lines = len(plan_structure_text.split('\n'))
            if current_y < y_margin + line_height_base * 10 or estimated_plan_lines > 30: # Estimación simple
                p.showPage(); current_y = height - y_margin
            
            draw_patient_section_title("Recetas Detalladas")

            for i_recipe_p, recipe in enumerate(parsed_recipes):
                app.logger.debug(f"PDF_DEBUG (crear_pdf_paciente): Dibujando receta paciente N°{i_recipe_p+1}: {recipe.get('name', 'Sin Nombre')}")
                if current_y < y_margin + line_height_base * 5: 
                    p.showPage(); current_y = height - y_margin
                    draw_patient_section_title("Recetas Detalladas (Cont.)")
                
                current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, f"{recipe.get('number', 'Receta N/N')}: {recipe.get('name', 'Sin Nombre')}", "Helvetica-Bold", 12, indent=10, line_spacing_factor=1.3)

                if recipe.get('servings'):
                    current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, f"Rinde: {recipe['servings']}", "Helvetica-Oblique", 10, indent=20, line_spacing_factor=1.2)
                if recipe.get('ingredients'):
                    current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, "Ingredientes:", "Helvetica-Bold", 10.5, indent=20, line_spacing_factor=1.2)
                    for ing in recipe['ingredients']:
                        ing_text = ing.get('raw_line', 'Ingrediente desconocido').lstrip('* ').strip()
                        current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, ing_text, "Helvetica", 10.5, indent=30, is_list_item=True, bullet="• ", line_spacing_factor=1.1)
                    current_y -= line_height_base * 0.3
                if recipe.get('instructions'):
                    current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, "Preparación:", "Helvetica-Bold", 10.5, indent=20, line_spacing_factor=1.2)
                    instruction_lines = str(recipe['instructions']).split('\n') # Asegurar que sea string
                    for i, instr_line in enumerate(instruction_lines):
                        instr_text = instr_line.strip()
                        is_numbered = re.match(r"^\s*\d+\.\s*", instr_text)
                        bullet_char = "" if is_numbered else "• " 
                        text_to_draw = instr_text 
                        current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, text_to_draw, "Helvetica", 10.5, indent=30, is_list_item=not is_numbered, bullet=bullet_char, line_spacing_factor=1.1)
                    current_y -= line_height_base * 0.3
                if recipe.get('condiments'):
                    current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, "Condimentos Sugeridos:", "Helvetica-Bold", 10.5, indent=20, line_spacing_factor=1.2)
                    current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, recipe['condiments'], "Helvetica", 10.5, indent=30, line_spacing_factor=1.1)
                    current_y -= line_height_base * 0.3
                if recipe.get('presentation'):
                    current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, "Sugerencia de Presentación/Servicio:", "Helvetica-Bold", 10.5, indent=20, line_spacing_factor=1.2)
                    current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, recipe['presentation'], "Helvetica", 10.5, indent=30, line_spacing_factor=1.1)
                current_y -= line_height_base * 0.8 
        else:
            current_y = draw_text_block_with_style(p, current_y, x_margin, max_width, line_height_base, "No se pudieron parsear las recetas detalladas.", "Helvetica-Italic", 10)

        p.save()
        buffer.seek(0)
        app.logger.info(f"PDF_DEBUG (crear_pdf_paciente): PDF para PACIENTE generado exitosamente para Evaluación ID: {evaluation_instance.id}")
        return buffer
    except Exception as pdf_error:
        app.logger.error(f"Error creando PDF para PACIENTE (Evaluación ID {evaluation_instance.id if evaluation_instance else 'N/A'}): {pdf_error}", exc_info=True)
        return io.BytesIO()

# (subir_a_drive_v2 - sin cambios)
def subir_a_drive_v2(pdf_buffer, nombre_archivo):
    if not DRIVE_FOLDER_ID:
        app.logger.error("ERROR: DRIVE_FOLDER_ID no configurado.")
        return None
    service = get_drive_service()
    if not service:
        app.logger.error("ERROR: No se pudo obtener servicio Drive.")
        return None
    file_metadata = {'name': nombre_archivo, 'parents': [DRIVE_FOLDER_ID]}
    pdf_buffer.seek(0)
    media = MediaIoBaseUpload(fd=pdf_buffer, mimetype='application/pdf', chunksize=1024*1024, resumable=True)
    try:
        file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        file_id = file.get("id")
        app.logger.info(f'Archivo subido a Drive con ID: {file_id}')
        return file_id
    except HttpError as error:
        app.logger.error(f'Error HttpError subiendo a Drive: {error}', exc_info=True)
        return None
    except Exception as e:
        app.logger.error(f'Error inesperado subiendo a Drive: {e}', exc_info=True)
        return None

# (enviar_email_con_adjunto - sin cambios)
def enviar_email_con_adjunto(destinatario, asunto, cuerpo_html, pdf_buffer, nombre_archivo_pdf):
    required_configs = ['MAIL_SERVER', 'MAIL_PORT', 'MAIL_USERNAME', 'MAIL_PASSWORD', 'MAIL_DEFAULT_SENDER']
    if not all(app.config.get(conf) for conf in required_configs):
        missing = [conf for conf in required_configs if not app.config.get(conf)]
        app.logger.error(f"Error: Faltan configs email: {', '.join(missing)}")
        return False
    
    remitente = app.config['MAIL_DEFAULT_SENDER']
    msg = MIMEMultipart('alternative')
    msg['From'] = remitente
    msg['To'] = destinatario
    msg['Subject'] = asunto
    msg.attach(MIMEText(cuerpo_html, 'html', 'utf-8'))

    # Modificado para manejar el caso donde no hay adjunto
    if pdf_buffer and nombre_archivo_pdf:
        pdf_buffer.seek(0)
        pdf_content = pdf_buffer.read()
        if pdf_content:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(pdf_content)
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{nombre_archivo_pdf}"')
            msg.attach(part)
    
    try:
        server = None
        if app.config['MAIL_USE_SSL']:
            import ssl
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(app.config['MAIL_SERVER'], app.config['MAIL_PORT'], context=context)
        elif app.config['MAIL_USE_TLS']:
            server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
        
        server.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
        server.sendmail(remitente, destinatario, msg.as_string())
        server.quit()
        app.logger.info(f"Email enviado a {destinatario}")
        return True
    except Exception as e:
        app.logger.error(f"Error enviando email: {e}", exc_info=True)
        return False

# --- Rutas de Flask ---

@app.route('/', methods=['GET'])
def index():
        # Redirigir la página de inicio directamente al dashboard de pacientes
    app.logger.info("Acceso a la raíz, redirigiendo al dashboard de pacientes.")
    return redirect(url_for('pacientes_dashboard'))

@app.route('/formulario_evaluacion', methods=['GET'])
@login_required
def formulario_evaluacion():
    """Renderiza la página para crear una evaluación o cargar una previa."""
    action = request.args.get('action', 'new_evaluation_new_patient')
    eval_id_str = request.args.get('load_evaluation_id')
    evaluation_data = None

    if action in ('edit_evaluation', 'load_eval_for_new') and eval_id_str:
        try:
            eval_id = int(eval_id_str)
            evaluation = Evaluation.query.get(eval_id)
            if evaluation and evaluation.patient:
                patient = evaluation.patient
                evaluation_data = {
                    'patient_id': patient.id,
                    'name': patient.name,
                    'surname': patient.surname,
                    'cedula': patient.cedula,
                    'email': patient.email,
                    'phone_number': patient.phone_number,
                    'education_level': patient.education_level,
                    'purchasing_power': patient.purchasing_power,
                    'dob': patient.dob.strftime('%Y-%m-%d') if patient.dob else None,
                    'sex': patient.sex,
                    'height_cm': patient.height_cm,
                    'allergies': patient.get_allergies(),
                    'intolerances': patient.get_intolerances(),
                    'preferences': patient.get_preferences(),
                    'aversions': patient.get_aversions(),

                    'evaluation_id': evaluation.id,
                    'consultation_date': evaluation.consultation_date.isoformat(),
                    'weight_at_eval': evaluation.weight_at_eval,
                    'wrist_circumference_cm': evaluation.wrist_circumference_cm,
                    'waist_circumference_cm': evaluation.waist_circumference_cm,
                    'hip_circumference_cm': evaluation.hip_circumference_cm,
                    'gestational_age_weeks': evaluation.gestational_age_weeks,
                    'activity_factor': evaluation.activity_factor,
                    'pathologies': evaluation.get_pathologies(),
                    'other_pathologies_text': evaluation.other_pathologies_text,
                    'postoperative_text': evaluation.postoperative_text,
                    'diet_type': evaluation.diet_type,
                    'other_diet_type_text': evaluation.other_diet_type_text,
                    'target_weight': evaluation.target_weight,
                    'target_waist_cm': evaluation.target_waist_cm,
                    'target_protein_perc': evaluation.target_protein_perc,
                    'target_carb_perc': evaluation.target_carb_perc,
                    'target_fat_perc': evaluation.target_fat_perc,
                    'edited_plan_text': evaluation.edited_plan_text,
                    'user_observations': evaluation.user_observations,
                    'micronutrients': evaluation.get_micronutrients(),
                    'base_foods': evaluation.get_base_foods(),
                    'references': evaluation.references,
                }
        except ValueError:
            app.logger.warning('ID de evaluación inválido para cargar datos')

    all_ingredients = Ingredient.query.order_by(Ingredient.name).all()
    ingredients_for_js = [{'id': ing.id, 'name': ing.name} for ing in all_ingredients]

    return render_template(
        'formulario_evaluacion.html',
        all_ingredients=ingredients_for_js,
        current_date_str=datetime.now(timezone.utc).strftime('%d/%m/%Y'),
        current_username=current_user.name or current_user.email,
        education_levels=app.config.get('EDUCATION_LEVELS', []),
        purchasing_power_levels=app.config.get('PURCHASING_POWER_LEVELS', []),
        activity_factors=app.config.get('ACTIVITY_FACTORS', []),
        available_pathologies=app.config.get('AVAILABLE_PATHOLOGIES', []),
        diet_types=app.config.get('DIET_TYPES', []),
        action=action,
        evaluation_data_to_load=evaluation_data
    )

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        id_token = request.form.get('idToken')
        if not id_token:
            flash('Token de autenticación faltante.', 'danger')
            return redirect(url_for('login_page'))
        try:
            decoded = auth.verify_id_token(id_token)
        except Exception as e:  # pragma: no cover - logging
            app.logger.error(f'Error verificando token Firebase: {e}')
            flash('Token de autenticación inválido.', 'danger')
            return redirect(url_for('login_page'))

        firebase_uid = decoded.get('uid')
        email = decoded.get('email')
        full_name = decoded.get('name', '')

        # Si existe un paciente con este UID o email, redirigir al portal del paciente
        patient = Patient.query.filter(
            or_(Patient.firebase_uid == firebase_uid, Patient.email == email)
        ).first()
        if patient:
            if not patient.firebase_uid:
                patient.firebase_uid = firebase_uid
                db.session.commit()
            app.logger.info(f"Login de paciente detectado para UID {firebase_uid} (Paciente ID {patient.id}).")
            return redirect(url_for('patient_dashboard_page'))

        user = User.query.filter_by(firebase_uid=firebase_uid).first()
        if not user:
            user = User.query.filter_by(email=email).first()
            if user:
                user.firebase_uid = firebase_uid
            else:
                name = ''
                surname = ''
                if full_name:
                    parts = full_name.split(' ', 1)
                    name = parts[0]
                    if len(parts) > 1:
                        surname = parts[1]
                user = User(firebase_uid=firebase_uid, email=email, name=name, surname=surname)
                db.session.add(user)
            db.session.commit()

        login_user(user)

        next_url = request.args.get('next')
        if next_url and is_safe_url(next_url):
            return redirect(next_url)
        return redirect(url_for('pacientes_dashboard'))

    return render_template('login.html')

# Route for registration page
@app.route('/register')
def register_page():
    return render_template('register.html')
# En app.py, añade esta nueva ruta:

@app.route('/api/preparations', methods=['POST'])
# @login_required # Descomentar si se implementa login
def create_user_preparation():
    data = request.get_json()
    if not data or not data.get('name'):
        return jsonify({'error': 'El nombre de la preparación es requerido.'}), 400

    app.logger.info(f"API CREATE_PREPARATION: Recibidos datos: {data}")

    new_preparation = UserPreparation(
        name=data.get('name'),
        description=data.get('description'),
        instructions=data.get('instructions'),
        preparation_type=data.get('preparation_type'),
        source=data.get('source', 'creada_usuario_manual') 
    )
    
    num_servings_from_data = data.get('num_servings')
    if num_servings_from_data is not None:
        try:
            num_servings_val = float(num_servings_from_data)
            new_preparation.num_servings = max(1.0, num_servings_val)
        except (ValueError, TypeError):
            app.logger.warning(f"Valor de num_servings inválido '{num_servings_from_data}' para nueva preparación. Usando 1.0.")
            new_preparation.num_servings = 1.0
    else:
        new_preparation.num_servings = 1.0

    ingredients_list_to_calculate = []
    if 'ingredients' in data:
        raw_ingredients_from_frontend = data['ingredients']
        cleaned_ingredients_for_db = []
        for ing_frontend in raw_ingredients_from_frontend:
            original_description = ing_frontend.get('item', '').strip()
            
            quantity_payload = ing_frontend.get('quantity')
            unit_payload = ing_frontend.get('unit')

            quantity_str_from_payload = str(quantity_payload).strip() if quantity_payload is not None else ""
            unit_str_from_payload = str(unit_payload).strip() if unit_payload is not None else ""
            
            parsed_components = _parse_ingredient_line(f"* {original_description}")
            parsed_item_name_from_desc = parsed_components['item']

            is_al_gusto_item = "al gusto" in original_description.lower() or \
                               original_description.lower() in ["sal y pimienta", "sal", "pimienta", "especias"]

            if is_al_gusto_item and not quantity_str_from_payload and not unit_str_from_payload:
                quantity_val = 1.0  
                unit_to_save = "pizca" 
                app.logger.info(f"CREATE_PREP: Item '{original_description}' detectado como 'al gusto' sin cantidad/unidad. Usando 1 pizca por defecto.")
            else:
                try:
                    quantity_val = float(quantity_str_from_payload) if quantity_str_from_payload else 0.0
                except ValueError:
                    quantity_val = 0.0
                    app.logger.warning(f"CREATE_PREP: ValueError convirtiendo cantidad '{quantity_str_from_payload}' para '{original_description}'. Usando 0.0.")
                
                unit_to_save = unit_str_from_payload if unit_str_from_payload else "N/A"
                if unit_to_save == "N/A" and quantity_val == 0.0 and not is_al_gusto_item:
                     app.logger.info(f"CREATE_PREP: Item '{original_description}' no es 'al gusto' y tiene cantidad 0 y unidad N/A.")
                elif unit_to_save == "N/A" and quantity_val != 0.0:
                     app.logger.warning(f"CREATE_PREP: Item '{original_description}' tiene cantidad {quantity_val} pero unidad N/A. Esto podría ser un error de entrada.")

            cleaned_ingredients_for_db.append({
                'original_description': original_description,
                'parsed_item_name': parsed_item_name_from_desc,
                'quantity': quantity_val,
                'unit': unit_to_save
            })

        new_preparation.set_ingredients(cleaned_ingredients_for_db)
        ingredients_list_to_calculate = cleaned_ingredients_for_db
    
    if 'suitability_tags' in data:
        new_preparation.set_suitability_tags(data['suitability_tags'])

    total_nutri = calculate_total_nutritional_info(ingredients_list_to_calculate)
    current_num_servings = new_preparation.num_servings 
    
    app.logger.debug(f"API CREATE_PREPARATION: Total nutri calculado: {total_nutri}, Num Servings: {current_num_servings}")

    new_preparation.calories_per_serving = round(total_nutri['calories'] / current_num_servings, 2) if current_num_servings > 0 else 0.0
    new_preparation.protein_g_per_serving = round(total_nutri['protein_g'] / current_num_servings, 2) if current_num_servings > 0 else 0.0
    new_preparation.carb_g_per_serving = round(total_nutri['carb_g'] / current_num_servings, 2) if current_num_servings > 0 else 0.0
    new_preparation.fat_g_per_serving = round(total_nutri['fat_g'] / current_num_servings, 2) if current_num_servings > 0 else 0.0
    new_preparation.set_micronutrients_per_serving(total_nutri['micros'])
    
    new_preparation.created_at = datetime.now(timezone.utc)
    new_preparation.updated_at = datetime.now(timezone.utc)

    try:
        db.session.add(new_preparation)
        db.session.commit()
        app.logger.info(f"Nueva preparación '{new_preparation.name}' (ID: {new_preparation.id}) creada.")
        return jsonify(new_preparation.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error al crear nueva preparación: {e}", exc_info=True)
        return jsonify({'error': 'Error interno al crear la preparación.'}), 500



@app.route('/pacientes_dashboard', methods=['GET'])
@login_required
def pacientes_dashboard():
    # Esta será la nueva página principal para la gestión de pacientes
    app.logger.info("Accediendo al dashboard de pacientes.")
    return render_template('pacientes_dashboard.html')

@app.route('/buscar_paciente', methods=['POST'])
def buscar_paciente():
    query = request.form.get('search_query', '').strip()
    results = []
    if not query:
        return jsonify({'error': 'Término de búsqueda vacío'}), 400
    app.logger.info(f"Buscando paciente con query: '{query}'")

    pacientes_encontrados = []
    # Intentar buscar por cédula si el query es numérico o parece una cédula
    if query.isdigit() or re.match(r"^\d{1,8}$", query): # Ajusta el regex si tus CIs tienen otro formato
        paciente_cedula = Patient.query.filter(Patient.cedula.ilike(f'%{query}%')).all() # Búsqueda parcial de CI
        pacientes_encontrados.extend(paciente_cedula)

    # Buscar por nombre y apellido si no se encontró por cédula o para complementar
    # Evitar duplicados si ya se encontró por CI
    cedulas_ya_encontradas = {p.cedula for p in pacientes_encontrados}
    search_term = f'%{query}%'
    pacientes_nombre_apellido = Patient.query.filter(
        (Patient.name.ilike(search_term) | Patient.surname.ilike(search_term)) & ~Patient.cedula.in_(cedulas_ya_encontradas)
    ).order_by(Patient.surname, Patient.name).limit(20).all()
    pacientes_encontrados.extend(pacientes_nombre_apellido)

    # Eliminar duplicados por si alguna lógica anterior los introdujo (aunque la actual debería prevenirlo)
    pacientes_encontrados = list({p.id: p for p in pacientes_encontrados}.values())
    # Ordenar finalmente
    pacientes_encontrados.sort(key=lambda p: (p.surname, p.name))

    if not pacientes_encontrados:
        app.logger.info("No se encontraron pacientes.")
        return jsonify({'message': 'No se encontraron pacientes.'}), 404

    for paciente in pacientes_encontrados:
        app.logger.info(f"Procesando paciente encontrado ID: {paciente.id}")
        patient_data = { 
            'id': paciente.id, 'name': paciente.name, 'surname': paciente.surname, 'cedula': paciente.cedula
        }
        # Por ahora, no necesitamos el historial completo de evaluaciones en la búsqueda simple del dashboard
        evaluations_history = [] 
        results.append({'patient': patient_data, 'evaluations': evaluations_history}) # evaluations estará vacío

    app.logger.info(f"Búsqueda devolvió {len(results)} paciente(s).")
    return jsonify({'results': results})

@app.route('/get_patient_details/<int:patient_id>')
def get_patient_details(patient_id):
    app.logger.info(f"Solicitando detalles para Paciente ID: {patient_id}")
    patient = Patient.query.get_or_404(patient_id)

    patient_data = {
        'id': patient.id,
        'name': patient.name,
        'surname': patient.surname,
        'cedula': patient.cedula,
        'email': patient.email,
        'phone_number': patient.phone_number,
        'education_level': patient.education_level,
        'purchasing_power': patient.purchasing_power,
        'dob': patient.dob.strftime('%Y-%m-%d') if patient.dob else None,
        'sex': patient.sex,
        'height_cm': patient.height_cm,
        'allergies': patient.get_allergies(),
        'intolerances': patient.get_intolerances(),
        'preferences': patient.get_preferences(),
        'aversions': patient.get_aversions(),
    }
    return jsonify(patient_data)

@app.route('/get_evaluation_data/<int:evaluation_id>')
def get_evaluation_data(evaluation_id):
    app.logger.info(f"Solicitando datos para Evaluación ID: {evaluation_id}")
    evaluation = Evaluation.query.get(evaluation_id)
    if not evaluation:
        app.logger.warning(f"Evaluación ID {evaluation_id} no encontrada.")
        abort(404, description="Evaluación no encontrada")

    patient = evaluation.patient 
    if not patient:
         app.logger.error(f"Evaluación ID {evaluation_id} no tiene paciente asociado.")
         abort(500, description="Error interno: datos inconsistentes.")

    evaluation_data = {
        'patient_id': patient.id,
        'name': patient.name, 'surname': patient.surname, 'cedula': patient.cedula,
        'email': patient.email,
        'phone_number': patient.phone_number,
        'education_level': patient.education_level,
        'purchasing_power': patient.purchasing_power,
        'dob': patient.dob.strftime('%Y-%m-%d') if patient.dob else None, 'sex': patient.sex,
        'height_cm': patient.height_cm, 
        'allergies': patient.get_allergies(), 'intolerances': patient.get_intolerances(),
        'preferences': patient.get_preferences(), 'aversions': patient.get_aversions(),
 
        'evaluation_id': evaluation.id, 
        'consultation_date': evaluation.consultation_date.isoformat(), 
        'weight_at_eval': evaluation.weight_at_eval,
        'wrist_circumference_cm': evaluation.wrist_circumference_cm,
        'waist_circumference_cm': evaluation.waist_circumference_cm,
        'hip_circumference_cm': evaluation.hip_circumference_cm,
        'gestational_age_weeks': evaluation.gestational_age_weeks,
        'activity_factor': evaluation.activity_factor,
        'pathologies': evaluation.get_pathologies(), 
        'other_pathologies_text': evaluation.other_pathologies_text,
        'postoperative_text': evaluation.postoperative_text,
        'diet_type': evaluation.diet_type,
        'other_diet_type_text': evaluation.other_diet_type_text,
        'target_weight': evaluation.target_weight,
        'target_waist_cm': evaluation.target_waist_cm,
        'target_protein_perc': evaluation.target_protein_perc,
        'target_carb_perc': evaluation.target_carb_perc,
        'target_fat_perc': evaluation.target_fat_perc,
        'edited_plan_text': evaluation.edited_plan_text, 
        'user_observations': evaluation.user_observations, 
        'micronutrients': evaluation.get_micronutrients(), 
        'base_foods': evaluation.get_base_foods(),
        'references': evaluation.references # Añadido para que /get_evaluation_data también devuelva referencias
    }
    app.logger.info(f"Devolviendo datos para Evaluación ID: {evaluation_id}")
    return jsonify(evaluation_data)

@app.route('/calcular_valores', methods=['POST'])
def calcular_valores():
    try: 
        data = request.json
        app.logger.info(f"BACKEND /calcular_valores: Datos JSON recibidos: {data}")

        # Validación de campos numéricos usando la función auxiliar
        try:
            height_cm = validate_numeric_field(data.get('height'), "Altura", min_val=30, max_val=300)
            weight_kg = validate_numeric_field(data.get('weight'), "Peso", min_val=1, max_val=500)
            wrist_cm = validate_numeric_field(data.get('wrist'), "Perímetro de muñeca", min_val=5, max_val=40)
            waist_cm = validate_numeric_field(data.get('waist'), "Perímetro de cintura", min_val=30, max_val=300)
            hip_cm = validate_numeric_field(data.get('hip'), "Perímetro de cadera", min_val=30, max_val=300)
            activity_factor_val = data.get('activity_factor', '1.2') # Tomar como string para validar
            activity_factor = validate_numeric_field(activity_factor_val, "Factor de actividad", allowed_values=[val[0] for val in app.config['ACTIVITY_FACTORS']])

        except ValueError as ve: # Captura errores de validate_numeric_field
            app.logger.warning(f"BACKEND /calcular_valores: Error de validación numérica: {ve}")
            return jsonify({'error': str(ve)}), 400
        except TypeError: # En caso de que algún .get() devuelva algo inesperado para str()
            app.logger.warning(f"BACKEND /calcular_valores: Error de tipo en datos de entrada: {data}")
            return jsonify({'error': 'Datos numéricos inválidos.'}), 400

        dob_str = data.get('dob')
        sex = data.get('sex')

        if not all(val is not None for val in [height_cm, weight_kg, activity_factor]) or \
           not (dob_str and dob_str.strip()) or \
           not (sex and sex.strip()):
            app.logger.warning(f"BACKEND /calcular_valores: Faltan datos básicos. "
                               f"H:{height_cm}, W:{weight_kg}, DOB:{dob_str}, S:{sex}, AF:{activity_factor}")
            return jsonify({'error': 'Faltan datos básicos (altura, peso, fecha nac., sexo, factor actividad)'}), 400

        try:
            dob_date = datetime.strptime(dob_str, '%Y-%m-%d').date()
        except ValueError:
            app.logger.warning(f"BACKEND /calcular_valores: Formato fecha DOB inválido: {dob_str}")
            return jsonify({'error': 'Formato de Fecha de Nacimiento inválido. Use YYYY-MM-DD.'}), 400
        
        age = Patient(dob=dob_date).calculate_age()
        if age is None:
            app.logger.warning(f"BACKEND /calcular_valores: No se pudo calcular la edad para DOB: {dob_str}")
            return jsonify({'error': 'Fecha de nacimiento no permite calcular edad válida.'}), 400

        app.logger.info("BACKEND /calcular_valores: Datos básicos presentes. Realizando cálculos.")

        imc = calculate_imc(weight_kg, height_cm)
        complexion = calculate_complexion(height_cm, wrist_cm, sex) if wrist_cm else None
        whr = calculate_waist_hip_ratio(waist_cm, hip_cm) if waist_cm and hip_cm else None
        whtr = calculate_waist_height_ratio(waist_cm, height_cm) if waist_cm else None
        ideal_weight_base = calculate_ideal_weight_devine(height_cm, sex)
        ideal_weight = adjust_ideal_weight_for_complexion(ideal_weight_base, complexion) if ideal_weight_base and complexion else ideal_weight_base
        tmb = calculate_tmb_mifflin(weight_kg, height_cm, age, sex)
        get = calculate_get(tmb, activity_factor)

        imc_risk = assess_imc_risk(imc) if imc is not None else None
        whr_risk = assess_whr_risk(whr, sex) if whr is not None else None
        whtr_risk = assess_whtr_risk(whtr) if whtr is not None else None

        reference_ranges = {
            'imc': {
                'Bajo Peso': '< 18.5', 'Normal': '18.5 - 24.9', 'Sobrepeso': '25.0 - 29.9',
                'Obesidad I': '30.0 - 34.9', 'Obesidad II': '35.0 - 39.9', 'Obesidad III': '>= 40.0',
                '_riesgo_actual_display': f"Riesgo Actual: {(imc_risk.capitalize() if imc_risk else 'N/A')}"
            },
            'whr': {
                'bajo_masculino': ('Riesgo Bajo (H)', '< 0.90'), 'medio_masculino': ('Riesgo Moderado (H)', '0.90 - 0.99'), 'alto_masculino': ('Riesgo Alto (H)', '>= 1.0'),
                'bajo_femenino': ('Riesgo Bajo (M)', '< 0.80'), 'medio_femenino': ('Riesgo Moderado (M)', '0.80 - 0.84'), 'alto_femenino': ('Riesgo Alto (M)', '>= 0.85'),
                '_riesgo_actual_display': f"Riesgo Actual: {(whr_risk.capitalize() if whr_risk else 'N/A')}"
            },
            'whtr': {
                'Saludable': '< 0.50', 'Riesgo Aumentado': '0.50 - 0.59', 'Alto Riesgo': '>= 0.60',
                '_riesgo_actual_display': f"Riesgo Actual: {(whtr_risk.capitalize() if whtr_risk else 'N/A')}"
            }
        }

        current_sex_for_whr = sex.lower() if sex else None
        specific_whr_ranges_for_response = {}
        if current_sex_for_whr:
            for key_whr, item_value in reference_ranges['whr'].items():
                if isinstance(item_value, tuple) and len(item_value) == 2:
                    label, value_range = item_value
                    if (current_sex_for_whr == 'masculino' and 'masculino' in key_whr) or \
                       (current_sex_for_whr == 'femenino' and 'femenino' in key_whr):
                        specific_whr_ranges_for_response[label] = value_range
        
        if whr_risk:
            specific_whr_ranges_for_response['Riesgo Actual (calculado)'] = whr_risk.capitalize()

        result = {
            'imc': imc, 'complexion': complexion,
            'waist_hip_ratio': whr, 'waist_height_ratio': whtr,
            'ideal_weight': ideal_weight, 'tmb': tmb, 'get': get,
            'imc_risk': imc_risk, 'whr_risk': whr_risk, 'whtr_risk': whtr_risk,
            'references': {
                'imc': reference_ranges['imc'], 
                'whr': specific_whr_ranges_for_response, 
                'whtr': reference_ranges['whtr'] 
            }
        }
        app.logger.info(f"BACKEND /calcular_valores: Cálculos y Referencias: {result}")
        return jsonify(result)

    except Exception as e: 
         app.logger.error(f"Error inesperado en /calcular_valores: {e}", exc_info=True)
         return jsonify({'error': 'Error interno del servidor al calcular valores.'}), 500

@app.route('/generar_plan', methods=['POST'])
def generar_plan_endpoint():
    data = request.json
    app.logger.info("Recibida solicitud para /generar_plan con datos detallados")
    app.logger.debug(f"Datos recibidos en /generar_plan: {data}")

    try:
        patient_id = data.get('patient_id') 
        name = data.get('name')
        surname = data.get('surname')
        cedula = data.get('cedula')
        dob_str = data.get('dob') 
        sex = data.get('sex')
        email = data.get('email')
        phone_number = data.get('phone_number')
        education_level = data.get('education_level')
        purchasing_power = data.get('purchasing_power')

        height_cm = float(data.get('height_cm')) if data.get('height_cm') else None
        weight_at_plan = float(data.get('weight_at_plan')) if data.get('weight_at_plan') else None
        wrist_cm = float(data.get('wrist_circumference_cm')) if data.get('wrist_circumference_cm') else None
        waist_cm = float(data.get('waist_circumference_cm')) if data.get('waist_circumference_cm') else None
        hip_cm = float(data.get('hip_circumference_cm')) if data.get('hip_circumference_cm') else None
        
        try:
            gestational_age_weeks = int(data.get('gestational_age_weeks', 0)) if data.get('gestational_age_weeks') else 0
        except (ValueError, TypeError):
            gestational_age_weeks = 0 
            app.logger.warning("Valor de edad gestacional no válido, usando 0.")

        activity_factor = float(data.get('activity_factor', 1.2)) if data.get('activity_factor') else 1.2

        pathologies = data.get('pathologies', []) 
        other_pathologies_text = data.get('other_pathologies_text', '')
        postoperative_text = data.get('postoperative_text', '')

        allergies = data.get('allergies', [])
        intolerances = data.get('intolerances', [])
        preferences = data.get('preferences', [])
        aversions = data.get('aversions', [])

        diet_type = data.get('diet_type')
        other_diet_type_text = data.get('other_diet_type_text', '')
        target_weight = float(data.get('target_weight')) if data.get('target_weight') else None
        target_waist_cm = float(data.get('target_waist_cm')) if data.get('target_waist_cm') else None
        target_protein_perc = float(data.get('target_protein_perc')) if data.get('target_protein_perc') else None
        target_carb_perc = float(data.get('target_carb_perc')) if data.get('target_carb_perc') else None
        target_fat_perc = float(data.get('target_fat_perc')) if data.get('target_fat_perc') else None
        
        micronutrients_from_request = data.get('micronutrients', {})
        base_foods_from_request = data.get('base_foods', [])

        required_for_plan_generation = [name, surname, cedula, dob_str, sex, height_cm, weight_at_plan, activity_factor, target_weight]
        if not all(field is not None and (not isinstance(field, str) or field.strip() != '') for field in required_for_plan_generation):
            app.logger.warning(f"BACKEND /generar_plan: Faltan datos esenciales. Datos recibidos: {data}")
            return jsonify({'error': 'Faltan datos esenciales del paciente o del plan para generar (Nombre, Apellido, CI, Fecha Nac, Sexo, Altura, Peso Actual, Factor Actividad, Peso Objetivo).'}), 400

        try:
            dob_date = datetime.strptime(dob_str, '%Y-%m-%d').date()
        except ValueError:
            app.logger.warning(f"BACKEND /generar_plan: Formato fecha DOB inválido: {dob_str}")
            return jsonify({'error': 'Formato de Fecha de Nacimiento inválido. Use YYYY-MM-DD.'}), 400
        
        age = Patient(dob=dob_date).calculate_age() 
        if age is None:
            app.logger.warning(f"BACKEND /generar_plan: No se pudo calcular la edad para DOB: {dob_str}")
            return jsonify({'error': 'Fecha de nacimiento no permite calcular edad válida.'}), 400

        imc = calculate_imc(weight_at_plan, height_cm)
        complexion = calculate_complexion(height_cm, wrist_cm, sex)
        waist_hip_ratio = calculate_waist_hip_ratio(waist_cm, hip_cm)
        waist_height_ratio = calculate_waist_height_ratio(waist_cm, height_cm)
        ideal_weight_base = calculate_ideal_weight_devine(height_cm, sex)
        ideal_weight = adjust_ideal_weight_for_complexion(ideal_weight_base, complexion)
        tmb = calculate_tmb_mifflin(weight_at_plan, height_cm, age, sex)
        get = calculate_get(tmb, activity_factor)
        imc_risk = assess_imc_risk(imc)
        whr_risk = assess_whr_risk(waist_hip_ratio, sex)
        whtr_risk = assess_whtr_risk(waist_height_ratio)

        plan_input_data_complete = {
            'patient_id': patient_id,
            'name': name, 'surname': surname, 'cedula': cedula, 'dob': dob_str, 'sex': sex,
            'email': email, 'phone_number': phone_number, 'education_level': education_level,
            'purchasing_power': purchasing_power, 'height_cm': height_cm,
            'weight_at_plan': weight_at_plan, 'wrist_circumference_cm': wrist_cm,
            'waist_circumference_cm': waist_cm, 'hip_circumference_cm': hip_cm,
            'gestational_age_weeks': gestational_age_weeks, 'activity_factor': activity_factor,
            'pathologies': pathologies, 'other_pathologies_text': other_pathologies_text,
            'postoperative_text': postoperative_text, 'allergies': allergies,
            'intolerances': intolerances, 'preferences': preferences, 'aversions': aversions,
            'diet_type': diet_type, 'other_diet_type_text': other_diet_type_text,
            'target_weight': target_weight, 'target_waist_cm': target_waist_cm,
            'target_protein_perc': target_protein_perc, 'target_carb_perc': target_carb_perc,
            'target_fat_perc': target_fat_perc,
            'age': age, 'calculated_imc': imc, 'calculated_complexion': complexion,
            'calculated_waist_hip_ratio': waist_hip_ratio, 'calculated_waist_height_ratio': waist_height_ratio,
            'calculated_ideal_weight': ideal_weight, 'tmb': tmb, 'calculated_calories': get,
            'imc_risk': imc_risk, 'whr_risk': whr_risk, 'whtr_risk': whtr_risk,
            'micronutrients': micronutrients_from_request, 
            'base_foods': base_foods_from_request,
            'references': data.get('references', {}) # <<< AÑADIR ESTA LÍNEA        
        } 

        app.logger.info("Llamando a generar_plan_nutricional_v2 con datos completos para el plan.")
        gemini_text_completo = generar_plan_nutricional_v2(plan_input_data_complete)

        if gemini_text_completo.startswith("Error:"):
             app.logger.error(f"Error devuelto por la función de generación de Gemini: {gemini_text_completo}")
             return jsonify({'error': gemini_text_completo}), 500

        app.logger.info("Plan generado exitosamente por Gemini.")
        return jsonify({
            'gemini_raw_text': gemini_text_completo, 
            'plan_data_for_save': plan_input_data_complete 
         })

    except ValueError as e:
        app.logger.error(f"Error de valor en /generar_plan (ej. float(), int(), strptime): {e}", exc_info=True)
        return jsonify({'error': f'Error en el formato de un dato al procesar el plan: {e}'}), 400
    except KeyError as e: 
        app.logger.error(f"Falta una clave esperada en los datos de /generar_plan: {e}", exc_info=True)
        return jsonify({'error': f'Falta el campo requerido: {e}'}), 400
    except Exception as e:
        app.logger.error(f"Error inesperado en /generar_plan: {e}", exc_info=True)
        traceback.print_exc() 
        return jsonify({'error': 'Ocurrió un error inesperado al generar el plan.'}), 500


@app.route('/paciente/<int:patient_id>/invitar', methods=['POST'])
@login_required
def invitar_paciente(patient_id):
    """
    Crea un usuario en Firebase para un paciente existente y le envía un email de invitación.
    """
    app.logger.info(f"Recibida solicitud para invitar al paciente ID: {patient_id}")
    patient = Patient.query.filter_by(id=patient_id, user_id=current_user.id).first_or_404()

    if not patient.email:
        app.logger.error(f"Intento de invitar al paciente ID {patient.id} sin email.")
        return jsonify({'error': 'El paciente no tiene un email registrado para poder invitarlo.'}), 400

    if patient.firebase_uid:
        app.logger.warning(f"Intento de invitar a un paciente ya invitado. Paciente ID: {patient.id}, UID: {patient.firebase_uid}")
        return jsonify({'error': 'Este paciente ya ha sido invitado y tiene una cuenta asociada.'}), 409

    try:
        # 1. Crear el usuario en Firebase Authentication
        app.logger.info(f"Creando usuario en Firebase para el email: {patient.email}")
        new_firebase_user = auth.create_user(
            email=patient.email,
            email_verified=False,
            display_name=f"{patient.name} {patient.surname}",
            disabled=False
        )
        app.logger.info(f"Usuario creado en Firebase con UID: {new_firebase_user.uid}")

        # 2. Vincular el UID de Firebase con el paciente en la base de datos local
        patient.firebase_uid = new_firebase_user.uid
        db.session.commit()
        app.logger.info(f"UID de Firebase vinculado al Paciente ID {patient.id} en la base de datos local.")

        # 3. Generar enlace para establecer contraseña y enviar email
        link = auth.generate_password_reset_link(patient.email)
        asunto = "¡Bienvenido/a a NutriApp! Configura tu cuenta."
        cuerpo_html = render_template('email/invitacion_paciente.html', patient_name=patient.name, action_url=link)
        
        enviado = enviar_email_con_adjunto(patient.email, asunto, cuerpo_html, None, None)

        if enviado:
            return jsonify({'message': f'Invitación enviada exitosamente a {patient.email}.'}), 200
        else:
            return jsonify({'error': 'Se creó la cuenta pero no se pudo enviar el email de invitación.'}), 500

    except Exception as e:
        app.logger.error(f"Error al invitar al paciente ID {patient.id}: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({'error': f'Error interno al procesar la invitación: {str(e)}'}), 500
    

# --- Rutas para el Portal del Paciente (Renderizado de Vistas) ---

@app.route('/patient/login')
def patient_login_page():
    """Renderiza la página de login para el paciente."""
    return render_template('patient_login.html')

@app.route('/patient/dashboard')
def patient_dashboard_page():
    """
    Renderiza el dashboard principal del paciente. 
    La página es un esqueleto; los datos se cargarán vía JavaScript usando una API protegida.
    """
    return render_template('patient_dashboard.html')

@app.route('/patient/shopping_list')
def patient_shopping_list_page():
    """
    Renders the shell page for the shopping list. Data is loaded via JS.
    """
    return render_template('patient_shopping_list.html')

# --- API para el Portal del Paciente ---

@app.route('/api/patient/me/latest_plan')
@patient_auth_required # Este decorador maneja la autenticación y carga g.patient
def get_my_latest_plan():
    """API para que un paciente logueado obtenga su último plan."""
    patient = g.patient
    app.logger.info(f"API: Solicitud de último plan para Paciente ID {patient.id}")

    latest_evaluation = patient.evaluations.order_by(Evaluation.consultation_date.desc()).first()

    if not latest_evaluation or not latest_evaluation.edited_plan_text:
        return jsonify({'message': 'Aún no tienes un plan de alimentación disponible.'}), 404

    return jsonify({
        'patient_name': f"{patient.name} {patient.surname}",
        'consultation_date': latest_evaluation.consultation_date.strftime('%d/%m/%Y'),
        'plan_text': latest_evaluation.edited_plan_text,
        'nutritionist_observations': latest_evaluation.user_observations or "Sin observaciones adicionales."
    })

@app.route('/api/patient/me/shopping_list')
@patient_auth_required
def get_my_shopping_list():
    """
    API that generates and returns the shopping list for the patient's latest plan.
    """
    patient = g.patient
    app.logger.info(f"API: Generando lista de compras para Paciente ID {patient.id}")

    latest_evaluation = patient.evaluations.order_by(Evaluation.consultation_date.desc()).first()

    if not latest_evaluation or not latest_evaluation.edited_plan_text:
        return jsonify({'error': 'No se encontró un plan para generar la lista de compras.'}), 404

    full_plan_text = latest_evaluation.edited_plan_text
    recetario_marker = "== RECETARIO DETALLADO =="
    plan_parts = full_plan_text.split(recetario_marker, 1)
    recetario_block_text = plan_parts[1].strip() if len(plan_parts) > 1 else ""

    if not recetario_block_text:
        return jsonify({'error': 'El plan no contiene un recetario detallado.'}), 404

    parsed_recipes = parse_all_recipes_from_text_block(recetario_block_text)
    
    # Diccionario para sumar las cantidades totales por ingrediente y unidad
    summed_ingredients = {}

    for recipe in parsed_recipes:
        for ingredient_data in recipe.get('ingredients', []):
            raw_line = ingredient_data.get('raw_line')
            if not raw_line: continue

            parsed_ingredient = _parse_ingredient_line(f"* {raw_line}")
            item_name = parsed_ingredient.get('item')
            quantity = parsed_ingredient.get('quantity')
            unit = parsed_ingredient.get('unit')

            if not item_name or quantity is None or unit is None or unit == "N/A": continue

            # Usar el nombre en minúsculas como clave para agrupar sin importar mayúsculas
            item_key = item_name.lower().strip()
            summed_ingredients.setdefault(item_key, {'display_name': item_name.capitalize(), 'units': {}})
            summed_ingredients[item_key]['units'].setdefault(unit, 0.0)
            summed_ingredients[item_key]['units'][unit] += quantity

    # --- Nueva Lógica de Categorización y Formateo ---
    pantry_items = ["aceite", "sal", "pimienta", "vinagre", "salsa de soja", "curry", "comino", "orégano", "laurel", "tomillo", "romero", "pimentón", "jengibre", "canela", "nuez moscada", "ajo en polvo", "cebolla en polvo", "caldo", "levadura", "miel", "azúcar", "edulcorante", "mostaza", "ketchup"]
    categories = {
        "Frutas y Verduras": [],
        "Proteínas (Carnes, Aves, Pescado, Tofu)": [],
        "Granos, Legumbres y Pasta": [],
        "Lácteos y Huevos": [],
        "Despensa (Aceites, Condimentos, Salsas, etc.)": [],
        "Otros": []
    }

    for item_key, data in summed_ingredients.items():
        display_name = data['display_name']
        
        # Determinar si es un item de despensa
        is_pantry_item = any(pantry_word in item_key for pantry_word in pantry_items)

        if is_pantry_item:
            formatted_item = display_name
            categories["Despensa (Aceites, Condimentos, Salsas, etc.)"].append(formatted_item)
        else:
            # Para no-despensa, mostrar cantidades sumadas
            quantities_str = ", ".join([f'{round(qty, 2) if qty % 1 != 0 else int(qty)} {unit}' for unit, qty in data['units'].items()])
            formatted_item = f"{display_name}: {quantities_str}"
            
            # Lógica simple de categorización (se puede mejorar)
            if any(w in item_key for w in ["pollo", "carne", "pescado", "salmón", "merluza", "atún", "tofu", "ternera", "cerdo", "pavo"]): categories["Proteínas (Carnes, Aves, Pescado, Tofu)"].append(formatted_item)
            elif any(w in item_key for w in ["arroz", "quinoa", "lenteja", "garbanzo", "fideo", "pasta", "pan"]): categories["Granos, Legumbres y Pasta"].append(formatted_item)
            elif any(w in item_key for w in ["leche", "queso", "yogur", "huevo"]): categories["Lácteos y Huevos"].append(formatted_item)
            elif any(w in item_key for w in ["agua"]): continue # Omitir agua de la lista de compras
            else: categories["Frutas y Verduras"].append(formatted_item) # Default para el resto

    return jsonify({
        'evaluation_date': latest_evaluation.consultation_date.strftime('%d/%m/%Y'),
        'shopping_list_items': categories
    })

@app.route('/guardar_evaluacion', methods=['POST'])
@login_required
def guardar_evaluacion():
    try:
        app.logger.info("Iniciando /guardar_evaluacion (NUEVA EVALUACIÓN)")
        data = request.json
        plan_data = data.get('plan_data') 
        edited_plan_text = data.get('edited_plan_text') 
        user_observations = data.get('user_observations', '')
        app.logger.debug(f"GUARDAR_EVALUACION (NUEVA): edited_plan_text recibido (primeros 300 chars): {edited_plan_text[:300] if edited_plan_text else 'None'}")

        if not plan_data or not edited_plan_text:
            return jsonify({'error': 'Faltan datos del plan o texto del plan.'}), 400

        cedula = plan_data.get('cedula')
        if not cedula: return jsonify({'error': 'Falta la cédula del paciente.'}), 400

        email_from_form = plan_data.get('email', '').strip()

        # LOG ADICIONAL PARA DEBUG
        patient_id_from_plan_data = plan_data.get('patient_id')
        app.logger.debug(f"GUARDAR_EVALUACION: patient_id recibido en plan_data: '{patient_id_from_plan_data}' (tipo: {type(patient_id_from_plan_data)})")

        if not patient_id_from_plan_data: # Modificado para usar la variable logueada
            if email_from_form: 
                existing_patient_by_email = Patient.query.filter(Patient.email == email_from_form).first()
                if existing_patient_by_email:
                    app.logger.warning(f"GUARDAR_EVALUACION: Conflicto de email. patient_id es None/vacío. Email '{email_from_form}' ya existe para Paciente ID {existing_patient_by_email.id}.")
                    return jsonify({'error': f"El email '{email_from_form}' ya está registrado para otro paciente ({existing_patient_by_email.name} {existing_patient_by_email.surname}, CI: {existing_patient_by_email.cedula}). Por favor, use un email diferente o verifique la cédula."}), 400

        if not plan_data.get('name') or len(plan_data.get('name', '')) < 2:
            return jsonify({'error': 'El nombre del paciente es requerido y debe tener al menos 2 caracteres.'}), 400
        if not plan_data.get('surname') or len(plan_data.get('surname', '')) < 2:
            return jsonify({'error': 'El apellido del paciente es requerido y debe tener al menos 2 caracteres.'}), 400
        if not re.match(r"^\d{1,15}$", cedula): 
            return jsonify({'error': 'La cédula debe ser numérica y tener un formato válido.'}), 400

        paciente = Patient.query.filter_by(cedula=cedula).first()
        dob_date = datetime.strptime(plan_data['dob'], '%Y-%m-%d').date() if plan_data.get('dob') else None
        patient_fields = { 
            'name': plan_data.get('name'), 'surname': plan_data.get('surname'), 'cedula': cedula.strip(),
            'dob': dob_date, 'sex': plan_data.get('sex'), 'height_cm': plan_data.get('height_cm'),
            'email': plan_data.get('email'), 'phone_number': plan_data.get('phone_number'),
            'education_level': plan_data.get('education_level'), 'purchasing_power': plan_data.get('purchasing_power')
        }
        patient_fields_clean = {k: v for k, v in patient_fields.items() if v is not None}

        if 'sex' in patient_fields_clean:
            sex_value_lower = patient_fields_clean['sex'].lower().strip()
            if sex_value_lower not in ['masculino', 'femenino', 'otro']:
                app.logger.error(f"Valor de sexo no válido recibido: '{patient_fields_clean['sex']}' (original), '{sex_value_lower}' (procesado)")
                return jsonify({'error': 'Valor de sexo no válido. Opciones válidas: Masculino, Femenino, Otro.'}), 400
            patient_fields_clean['sex'] = sex_value_lower
        if 'education_level' in patient_fields_clean and patient_fields_clean['education_level'] not in [val[0] for val in app.config['EDUCATION_LEVELS']]:
            return jsonify({'error': 'Nivel educativo no válido.'}), 400
        if 'purchasing_power' in patient_fields_clean and patient_fields_clean['purchasing_power'] not in [val[0] for val in app.config['PURCHASING_POWER_LEVELS']]:
            return jsonify({'error': 'Nivel de poder adquisitivo no válido.'}), 400
        if 'height_cm' in patient_fields_clean:
            height_str = str(patient_fields_clean['height_cm']).strip()
            if not height_str:
                patient_fields_clean['height_cm'] = None
            else:
                try:
                    height_val = float(height_str)
                    if not (30 < height_val < 300):
                        return jsonify({'error': 'Altura no válida (debe estar entre 30 y 300 cm).'}), 400
                    patient_fields_clean['height_cm'] = height_val
                except ValueError:
                    return jsonify({'error': 'Altura debe ser un número.'}), 400
        if 'email' in patient_fields_clean and patient_fields_clean['email'] and not re.match(r"[^@]+@[^@]+\.[^@]+", patient_fields_clean['email']):
            return jsonify({'error': 'Formato de email no válido.'}), 400

        if paciente: 
            app.logger.info(f"Actualizando datos Paciente ID {paciente.id}")
            for key, value in patient_fields_clean.items(): setattr(paciente, key, value)
            paciente.set_allergies(plan_data.get('allergies', []))
            paciente.set_intolerances(plan_data.get('intolerances', []))
            paciente.set_preferences(plan_data.get('preferences', []))
            paciente.set_aversions(plan_data.get('aversions', []))
            if email_from_form and paciente.email != email_from_form:
                existing_patient_by_email = Patient.query.filter(Patient.email == email_from_form, Patient.id != paciente.id).first()
                if existing_patient_by_email:
                    return jsonify({'error': f"El email '{email_from_form}' ya está registrado para otro paciente. No se puede actualizar."}), 400
        else: 
            app.logger.info("Creando nuevo Paciente")
            paciente = Patient(**patient_fields_clean)
            paciente.user_id = current_user.id
            paciente.set_allergies(plan_data.get('allergies', [])); paciente.set_intolerances(plan_data.get('intolerances', []))
            paciente.set_preferences(plan_data.get('preferences', [])); paciente.set_aversions(plan_data.get('aversions', []))
            db.session.add(paciente)
        db.session.commit() 
        app.logger.info(f"Paciente ID {paciente.id} listo.")

        v_data = {}
        try:
            v_data['weight_at_eval'] = validate_numeric_field(plan_data.get('weight_at_plan'), "Peso en evaluación", min_val=1, max_val=500)
            v_data['wrist_circumference_cm'] = validate_numeric_field(plan_data.get('wrist_circumference_cm'), "Perímetro de muñeca", min_val=5, max_val=40)
            v_data['waist_circumference_cm'] = validate_numeric_field(plan_data.get('waist_circumference_cm'), "Perímetro de cintura", min_val=30, max_val=300)
            v_data['hip_circumference_cm'] = validate_numeric_field(plan_data.get('hip_circumference_cm'), "Perímetro de cadera", min_val=30, max_val=300)
            v_data['gestational_age_weeks'] = validate_numeric_field(plan_data.get('gestational_age_weeks'), "Edad gestacional", type_converter=int, min_val=0, max_val=45)
            v_data['activity_factor'] = validate_numeric_field(plan_data.get('activity_factor'), "Factor de actividad", allowed_values=[val[0] for val in app.config['ACTIVITY_FACTORS']])
            v_data['target_weight'] = validate_numeric_field(plan_data.get('target_weight'), "Peso objetivo", min_val=1, max_val=500)
            v_data['target_waist_cm'] = validate_numeric_field(plan_data.get('target_waist_cm'), "Cintura objetivo", min_val=30, max_val=300)
            v_data['target_protein_perc'] = validate_numeric_field(plan_data.get('target_protein_perc'), "Porcentaje de proteínas", min_val=0, max_val=100)
            v_data['target_carb_perc'] = validate_numeric_field(plan_data.get('target_carb_perc'), "Porcentaje de carbohidratos", min_val=0, max_val=100)
            v_data['target_fat_perc'] = validate_numeric_field(plan_data.get('target_fat_perc'), "Porcentaje de grasas", min_val=0, max_val=100)
            
            micronutrients_input = plan_data.get('micronutrients', {})
            v_data['mic_k'] = validate_numeric_field(micronutrients_input.get('potassium_mg'), "Potasio (mg)", min_val=0, max_val=10000)
            v_data['mic_ca'] = validate_numeric_field(micronutrients_input.get('calcium_mg'), "Calcio (mg)", min_val=0, max_val=5000)
            v_data['mic_na'] = validate_numeric_field(micronutrients_input.get('sodium_mg'), "Sodio (mg)", min_val=0, max_val=5000) 
            v_data['mic_chol'] = validate_numeric_field(micronutrients_input.get('cholesterol_mg'), "Colesterol (mg)", min_val=0, max_val=1000)

            macros_sum = sum(filter(None, [v_data['target_protein_perc'], v_data['target_carb_perc'], v_data['target_fat_perc']]))
            if macros_sum > 0 and not (98 <= macros_sum <= 102):
                 app.logger.warning(f"Suma de macros ({macros_sum}%) fuera del rango esperado (98-102%).")
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400

        app.logger.info(f"Creando NUEVA Evaluación para Paciente ID {paciente.id}")
        nueva_evaluacion = Evaluation(
            patient_id=paciente.id,
            user_id=current_user.id,
            weight_at_eval=v_data.get('weight_at_eval'),
            wrist_circumference_cm=v_data.get('wrist_circumference_cm'),
            waist_circumference_cm=v_data.get('waist_circumference_cm'),
            hip_circumference_cm=v_data.get('hip_circumference_cm'),
            gestational_age_weeks=v_data.get('gestational_age_weeks', 0),
            activity_factor=v_data.get('activity_factor', 1.2),
            calculated_imc=plan_data.get('calculated_imc'),
            calculated_complexion=plan_data.get('calculated_complexion'),
            calculated_waist_hip_ratio=plan_data.get('calculated_waist_hip_ratio'),
            calculated_waist_height_ratio=plan_data.get('calculated_waist_height_ratio'),
            calculated_ideal_weight=plan_data.get('calculated_ideal_weight'),
            calculated_calories=plan_data.get('calculated_calories'),
            imc_risk=plan_data.get('imc_risk'),
            whr_risk=plan_data.get('whr_risk'),
            whtr_risk=plan_data.get('whtr_risk'), 
            other_pathologies_text=plan_data.get('other_pathologies_text'),
            postoperative_text=plan_data.get('postoperative_text'),
            diet_type=plan_data.get('diet_type'),
            other_diet_type_text=plan_data.get('other_diet_type_text'),
            target_weight=v_data.get('target_weight'),
            target_waist_cm=v_data.get('target_waist_cm'),
            target_protein_perc=v_data.get('target_protein_perc'),
            target_carb_perc=v_data.get('target_carb_perc'),
            target_fat_perc=v_data.get('target_fat_perc'),
            edited_plan_text=edited_plan_text, 
            user_observations=user_observations,
            structured_plan_input_json=json.dumps(plan_data) 
        )
        nueva_evaluacion.set_pathologies(plan_data.get('pathologies', []))
        micronutrients_to_set = {
            'potassium_mg': v_data.get('mic_k'), 'calcium_mg': v_data.get('mic_ca'),
            'sodium_mg': v_data.get('mic_na'), 'cholesterol_mg': v_data.get('mic_chol')
        }
        nueva_evaluacion.set_micronutrients({k: v for k, v in micronutrients_to_set.items() if v is not None})
        references_from_frontend = plan_data.get('references')
        app.logger.info(f"Finalizar: Datos de 'references' recibidos del frontend: {references_from_frontend}")
        if references_from_frontend:
            nueva_evaluacion.references = references_from_frontend

        db.session.add(nueva_evaluacion)
        db.session.commit() 
        app.logger.info(f"NUEVA Evaluación ID {nueva_evaluacion.id} creada.")
        nueva_evaluacion.set_base_foods(plan_data.get('base_foods', []))
        db.session.commit()

        selected_favorite_recipes_titles = data.get('selected_favorite_recipes', [])
        full_plan_text_for_favorites = edited_plan_text

        if selected_favorite_recipes_titles and full_plan_text_for_favorites:
            app.logger.info(f"GUARDAR_EVALUACION: Procesando {len(selected_favorite_recipes_titles)} recetas favoritas seleccionadas.")
            
            # Determinar el tag basado en el tipo de dieta del plan
            tag_from_diet_type = None
            other_diet_text = plan_data.get('other_diet_type_text', '').strip()
            if other_diet_text:
                tag_from_diet_type = other_diet_text.lower().strip().replace(" ", "_") # Normalizar
            elif plan_data.get('diet_type'):
                tag_from_diet_type = plan_data.get('diet_type').lower().strip().replace(" ", "_") # Normalizar

            for recipe_title_from_frontend in selected_favorite_recipes_titles:
                match_num_part = re.search(r"(N°\d+)", recipe_title_from_frontend, re.IGNORECASE)
                recipe_identifier_for_parse = None
                
                if match_num_part:
                    recipe_identifier_for_parse = match_num_part.group(1).strip()
                    app.logger.debug(f"FAV_SAVE (GuardarEval): Identificador NUMÉRICO extraído: '{recipe_identifier_for_parse}' de '{recipe_title_from_frontend}'")
                else: 
                    match_title_only = re.match(r"Receta\s*(?:N°|No\.|N\.)?\s*\d*:\s*(.+)", recipe_title_from_frontend, re.IGNORECASE)
                    if match_title_only:
                        recipe_identifier_for_parse = match_title_only.group(1).strip().rstrip('.').strip()
                    else:
                        recipe_identifier_for_parse = recipe_title_from_frontend.strip().rstrip('.').strip()
                    app.logger.warning(f"FAV_SAVE (GuardarEval): No se pudo extraer 'N°X' de '{recipe_title_from_frontend}'. Usando nombre: '{recipe_identifier_for_parse}' para parseo.")
                
                app.logger.debug(f"FAV_SAVE (GuardarEval): Identificador FINAL para parseo: '{recipe_identifier_for_parse}'")
                parsed_recipe_data = parse_recipe_from_text(recipe_identifier_for_parse, full_plan_text_for_favorites)

                if parsed_recipe_data:
                    actual_recipe_name_from_recetario_log = parsed_recipe_data.get('name', 'Nombre no encontrado en parseo')
                    app.logger.debug(f"FAV_SAVE (GuardarEval): Receta con identificador '{recipe_identifier_for_parse}' (Nombre real: '{actual_recipe_name_from_recetario_log}') PARSEADA. "
                                     f"Ingredientes: {bool(parsed_recipe_data.get('ingredients'))}, "
                                     f"Num_servings: {parsed_recipe_data.get('num_servings')}")
                    if not parsed_recipe_data.get('ingredients'):
                        app.logger.warning(f"FAV_SAVE (GuardarEval): Receta con identificador '{recipe_identifier_for_parse}' (Nombre real: '{actual_recipe_name_from_recetario_log}') parseada PERO SIN INGREDIENTES. No se guardará.")
                else:
                    app.logger.warning(f"FAV_SAVE (GuardarEval): Falló el parseo de la receta con identificador '{recipe_identifier_for_parse}' (de: '{recipe_title_from_frontend}'). No se guardará.")

                if parsed_recipe_data and parsed_recipe_data.get('ingredients'): 
                    actual_recipe_name_from_recetario = parsed_recipe_data.get('name')
                    if not actual_recipe_name_from_recetario:
                        app.logger.error(f"FAV_SAVE (GuardarEval): 'parsed_recipe_data' no tiene 'name' para identificador '{recipe_identifier_for_parse}'. Saltando guardado.")
                        continue
                    try:
                        existing_fav = UserPreparation.query.filter_by(name=actual_recipe_name_from_recetario).first()
                        if existing_fav:
                            app.logger.info(f"GUARDAR_EVALUACION: Receta favorita '{existing_fav.name}' ya existe. Omitiendo duplicado.")
                            continue
                        
                        servings_value_from_parse_fav = parsed_recipe_data.get('num_servings', 1.0)
                        try:
                            num_servings_for_prep_fav = float(servings_value_from_parse_fav)
                        except (ValueError, TypeError):
                            app.logger.warning(f"FAV_SAVE (GuardarEval - Favorita): num_servings '{servings_value_from_parse_fav}' no es un float válido. Usando 1.0.")
                            num_servings_for_prep_fav = 1.0
                            
                        new_preparation = UserPreparation(
                            name=actual_recipe_name_from_recetario,
                            description=parsed_recipe_data.get('description', "Receta generada por IA y marcada como favorita."),
                            instructions=parsed_recipe_data.get('instructions', "Instrucciones no parseadas."),
                            preparation_type=parsed_recipe_data.get('preparation_type', 'almuerzo'), 
                            num_servings=num_servings_for_prep_fav,
                            source='favorita_ia_finalizar'
                        )
                        
                        # Asignar el tag basado en el tipo de dieta del plan
                        if tag_from_diet_type:
                            new_preparation.set_suitability_tags([tag_from_diet_type])
                            app.logger.info(f"FAV_SAVE (GuardarEval): Asignando tag '{tag_from_diet_type}' a la preparación '{new_preparation.name}'.")
                        else:
                            new_preparation.set_suitability_tags([]) 
                            app.logger.info(f"FAV_SAVE (GuardarEval): No se asignó tag de tipo de dieta a '{new_preparation.name}' (tipo de dieta del plan no especificado o 'Otra' sin texto).")

                        db_ready_ingredients = []
                        for ing_from_parse in parsed_recipe_data.get('ingredients', []):
                            db_ready_ingredients.append({
                                'original_description': ing_from_parse.get('original_line', ing_from_parse.get('item', '')),
                                'parsed_item_name': ing_from_parse.get('item', ''),
                                'quantity': ing_from_parse.get('quantity', 0.0),
                                'unit': ing_from_parse.get('unit', 'N/A')
                            })
                        new_preparation.set_ingredients(db_ready_ingredients)
                        
                        ingredients_for_calc_fav = parsed_recipe_data.get('ingredients', [])
                        total_nutri_fav = calculate_total_nutritional_info(ingredients_for_calc_fav)
                        
                        num_servings_fav = new_preparation.num_servings 
                        if not (isinstance(num_servings_fav, (int, float)) and num_servings_fav > 0):
                            app.logger.warning(f"FAV_SAVE (GuardarEval - Favorita): num_servings_fav ({num_servings_fav}) no es un número positivo para receta '{new_preparation.name}'. Usando 1.0 para cálculo.")
                            num_servings_fav = 1.0
                        
                        new_preparation.calories_per_serving = round(total_nutri_fav['calories'] / num_servings_fav, 2) if num_servings_fav != 0 else 0.0
                        new_preparation.protein_g_per_serving = round(total_nutri_fav['protein_g'] / num_servings_fav, 2) if num_servings_fav != 0 else 0.0
                        new_preparation.carb_g_per_serving = round(total_nutri_fav['carb_g'] / num_servings_fav, 2) if num_servings_fav != 0 else 0.0
                        new_preparation.fat_g_per_serving = round(total_nutri_fav['fat_g'] / num_servings_fav, 2) if num_servings_fav != 0 else 0.0
                        new_preparation.set_micronutrients_per_serving(total_nutri_fav['micros'])
                        
                        db.session.add(new_preparation)
                        app.logger.info(f"FAV_SAVE (GuardarEval): Receta '{new_preparation.name}' preparada para guardar como favorita.")
                    except Exception as fav_save_e:
                        app.logger.error(f"FAV_SAVE (GuardarEval): Error al procesar la receta favorita '{actual_recipe_name_from_recetario}': {fav_save_e}", exc_info=True)
        
        # Commit general después de procesar todas las favoritas y la evaluación principal
        # Este commit también guardará los cambios en el paciente si fue actualizado.
        try:
            db.session.commit()
            app.logger.info("GUARDAR_EVALUACION: Commit final realizado exitosamente.")
        except Exception as final_commit_e:
            db.session.rollback()
            app.logger.error(f"GUARDAR_EVALUACION: Error en el commit final: {final_commit_e}", exc_info=True)
            # Considerar si se debe retornar un error al frontend aquí
            # return jsonify({'error': 'Error crítico al guardar los datos finales.'}), 500
            # Por ahora, permitimos que continúe para la generación del PDF, pero el estado de la BD puede ser inconsistente.
            flash('Error crítico al guardar todos los datos. Algunos cambios podrían no haberse guardado.', 'danger')
            
        app.logger.info(f"Generando PDF para Evaluación ID: {nueva_evaluacion.id}")
        nombre_pdf_completo = f"EvaluacionNutricional_COMPLETA_{paciente.surname}_{paciente.cedula}_{nueva_evaluacion.consultation_date.strftime('%Y%m%d_%H%M')}.pdf"
        pdf_buffer_completo = crear_pdf_v2(nueva_evaluacion)
        pdf_completo_generado_ok = pdf_buffer_completo.getbuffer().nbytes > 0

        drive_file_id = None
        if pdf_completo_generado_ok:
             drive_file_id = subir_a_drive_v2(pdf_buffer_completo, nombre_pdf_completo)
             if drive_file_id:
                  nueva_evaluacion.pdf_storage_path = drive_file_id
                  db.session.commit()
                  app.logger.info("PDF Completo subido a Drive y ID guardado.")
             else: 
                  flash('Advertencia: No se pudo subir el PDF a Google Drive.', 'warning')
        
        nombre_pdf_paciente = f"PlanNutricional_{paciente.surname}_{paciente.cedula}_{nueva_evaluacion.consultation_date.strftime('%Y%m%d')}.pdf"
        pdf_buffer_paciente = crear_pdf_paciente(nueva_evaluacion)
        pdf_paciente_generado_ok = pdf_buffer_paciente.getbuffer().nbytes > 0

        if not pdf_paciente_generado_ok and paciente.email:
            flash('Advertencia: Se guardó la evaluación, pero no se pudo generar el PDF para enviar al paciente.', 'warning')
        
        msg = f"Nueva Evaluación (ID: {nueva_evaluacion.id}) para {paciente.name} guardada."
        if pdf_completo_generado_ok: msg += " PDF completo generado."
        if drive_file_id: msg += " PDF completo subido a Drive."
        if pdf_paciente_generado_ok and paciente.email: msg += " PDF para paciente generado y listo para enviar."
        elif pdf_paciente_generado_ok and not paciente.email: msg += " PDF para paciente generado (paciente sin email)."

        app.logger.info(f"Finalización completada para Evaluación ID {nueva_evaluacion.id}")
        return jsonify({
            'message': msg, 
            'patient_id': paciente.id, 
            'evaluation_id': nueva_evaluacion.id,
            'patient_email': paciente.email,
            'pdf_paciente_generado_ok': pdf_paciente_generado_ok
        })

    except ValueError as e:
        app.logger.error(f"Error de valor en /finalizar: {e}", exc_info=True)
        return jsonify({'error': f'Error en formato de dato: {e}'}), 400
    except Exception as e: 
        app.logger.error(f"Error CATASTRÓFICO en /finalizar: {e}", exc_info=True)
        try: db.session.rollback(); app.logger.info("Rollback realizado.")
        except Exception as rb_exc: app.logger.error(f"Error en rollback: {rb_exc}")
        traceback.print_exc()
        return jsonify({'error': 'Error inesperado al guardar la evaluación.'}), 500

@app.route('/actualizar_evaluacion/<int:evaluation_id>', methods=['PUT'])
@login_required
def actualizar_evaluacion_endpoint(evaluation_id):
    evaluation = Evaluation.query.get_or_404(evaluation_id)
    if evaluation.user_id != current_user.id:
        app.logger.error(f"Acceso denegado: Usuario {current_user.id} intentó editar evaluación {evaluation.id} del usuario {evaluation.user_id}.")
        abort(403)
    patient = evaluation.patient
    if not patient:
        app.logger.error(f"Error crítico: Evaluación ID {evaluation_id} no tiene paciente asociado para actualizar.")
        return jsonify({'error': "Error: La evaluación no está asociada a ningún paciente."}), 500

    try:
        app.logger.info(f"Iniciando PUT para actualizar Evaluación ID: {evaluation_id}")
        data = request.json
        app.logger.debug(f"ACTUALIZAR_EVALUACION - Raw data received: {json.dumps(data, indent=2, ensure_ascii=False)}")
        plan_data = data.get('plan_data') 
        edited_plan_text = data.get('edited_plan_text')
        user_observations = data.get('user_observations', '')

        if not plan_data or not edited_plan_text:
            app.logger.warning(f"Faltan datos del plan o texto del plan al actualizar Evaluación ID: {evaluation_id}")
            return jsonify({'error': 'Faltan datos del plan o texto del plan.'}), 400
        app.logger.debug(f"ACTUALIZAR_EVALUACION - plan_data extraído: {json.dumps(plan_data, indent=2, ensure_ascii=False)}")        
        
        patient.name = plan_data.get('name', patient.name)
        patient.surname = plan_data.get('surname', patient.surname)
        if plan_data.get('dob'):
            try:
                patient.dob = datetime.strptime(plan_data['dob'], '%Y-%m-%d').date()
            except (ValueError, TypeError) as dob_err:
                app.logger.warning(f"Error parsing DOB '{plan_data['dob']}' during update: {dob_err}")
        patient.sex = plan_data.get('sex', patient.sex)
        patient.height_cm = plan_data.get('height_cm', patient.height_cm)
        
        new_email = plan_data.get('email', '').strip()
        if new_email and new_email != patient.email:
            existing_patient_with_new_email = Patient.query.filter(Patient.email == new_email, Patient.id != patient.id).first()
            if existing_patient_with_new_email:
                return jsonify({'error': f"El email '{new_email}' ya está registrado para otro paciente."}), 400
        patient.email = new_email if new_email else patient.email

        patient.phone_number = plan_data.get('phone_number', patient.phone_number)
        patient.education_level = plan_data.get('education_level', patient.education_level)
        patient.purchasing_power = plan_data.get('purchasing_power', patient.purchasing_power)
        patient.set_allergies(plan_data.get('allergies', patient.get_allergies()))
        patient.set_intolerances(plan_data.get('intolerances', patient.get_intolerances()))
        patient.set_preferences(plan_data.get('preferences', patient.get_preferences()))
        patient.set_aversions(plan_data.get('aversions', patient.get_aversions()))

        v_data = {}
        try:
            v_data['weight_at_eval'] = validate_numeric_field(plan_data.get('weight_at_plan'), "Peso en evaluación", min_val=1, max_val=500)
            v_data['height_cm_eval'] = validate_numeric_field(plan_data.get('height_cm'), "Altura en evaluación", min_val=30, max_val=300)
            v_data['wrist_circumference_cm'] = validate_numeric_field(plan_data.get('wrist_circumference_cm'), "Perímetro de muñeca", min_val=5, max_val=40)
            v_data['gestational_age_weeks'] = validate_numeric_field(plan_data.get('gestational_age_weeks'), "Edad gestacional", type_converter=int, min_val=0, max_val=45)
            v_data['activity_factor'] = validate_numeric_field(plan_data.get('activity_factor'), "Factor de actividad", allowed_values=[val[0] for val in app.config['ACTIVITY_FACTORS']])
            v_data['target_weight'] = validate_numeric_field(plan_data.get('target_weight'), "Peso objetivo", min_val=1, max_val=500)
            v_data['target_protein_perc'] = validate_numeric_field(plan_data.get('target_protein_perc'), "Porcentaje de proteínas", min_val=0, max_val=100)
            v_data['target_carb_perc'] = validate_numeric_field(plan_data.get('target_carb_perc'), "Porcentaje de carbohidratos", min_val=0, max_val=100)
            v_data['target_fat_perc'] = validate_numeric_field(plan_data.get('target_fat_perc'), "Porcentaje de grasas", min_val=0, max_val=100)
            micronutrients_input = plan_data.get('micronutrients', {})
            v_data['mic_k'] = validate_numeric_field(micronutrients_input.get('potassium_mg'), "Potasio (mg)", min_val=0, max_val=10000)
            v_data['mic_ca'] = validate_numeric_field(micronutrients_input.get('calcium_mg'), "Calcio (mg)", min_val=0, max_val=5000)
            v_data['mic_na'] = validate_numeric_field(micronutrients_input.get('sodium_mg'), "Sodio (mg)", min_val=0, max_val=5000)
            v_data['mic_chol'] = validate_numeric_field(micronutrients_input.get('cholesterol_mg'), "Colesterol (mg)", min_val=0, max_val=1000)
        except ValueError as ve:
            return jsonify({'error': str(ve)}), 400

        evaluation.weight_at_eval = v_data.get('weight_at_eval', evaluation.weight_at_eval)
        evaluation.wrist_circumference_cm = v_data.get('wrist_circumference_cm', evaluation.wrist_circumference_cm)
        evaluation.waist_circumference_cm = v_data.get('waist_circumference_cm', evaluation.waist_circumference_cm)
        evaluation.hip_circumference_cm = v_data.get('hip_circumference_cm', evaluation.hip_circumference_cm)
        evaluation.gestational_age_weeks = v_data.get('gestational_age_weeks', evaluation.gestational_age_weeks)
        evaluation.activity_factor = v_data.get('activity_factor', evaluation.activity_factor)
        evaluation.calculated_imc=plan_data.get('calculated_imc', evaluation.calculated_imc)
        evaluation.calculated_complexion=plan_data.get('calculated_complexion', evaluation.calculated_complexion)
        evaluation.calculated_waist_hip_ratio=plan_data.get('calculated_waist_hip_ratio', evaluation.calculated_waist_hip_ratio)
        evaluation.calculated_waist_height_ratio=plan_data.get('calculated_waist_height_ratio', evaluation.calculated_waist_height_ratio)
        evaluation.calculated_ideal_weight=plan_data.get('calculated_ideal_weight', evaluation.calculated_ideal_weight)
        evaluation.calculated_calories=plan_data.get('calculated_calories', evaluation.calculated_calories)
        evaluation.imc_risk=plan_data.get('imc_risk', evaluation.imc_risk)
        evaluation.whr_risk=plan_data.get('whr_risk', evaluation.whr_risk)
        evaluation.whtr_risk=plan_data.get('whtr_risk', evaluation.whtr_risk)
        evaluation.set_pathologies(plan_data.get('pathologies', evaluation.get_pathologies()))
        evaluation.other_pathologies_text = plan_data.get('other_pathologies_text', evaluation.other_pathologies_text)
        evaluation.postoperative_text = plan_data.get('postoperative_text', evaluation.postoperative_text)
        evaluation.diet_type = plan_data.get('diet_type', evaluation.diet_type)
        evaluation.other_diet_type_text = plan_data.get('other_diet_type_text', evaluation.other_diet_type_text)
        evaluation.target_weight = v_data.get('target_weight', evaluation.target_weight)
        evaluation.target_protein_perc = v_data.get('target_protein_perc', evaluation.target_protein_perc)
        evaluation.target_carb_perc = v_data.get('target_carb_perc', evaluation.target_carb_perc)
        evaluation.target_fat_perc = v_data.get('target_fat_perc', evaluation.target_fat_perc)
        evaluation.edited_plan_text = edited_plan_text
        evaluation.user_observations = user_observations
        evaluation.structured_plan_input_json = json.dumps(plan_data)
        references_from_payload = plan_data.get('references')
        app.logger.debug(f"ACTUALIZAR_EVALUACION - References from payload: {json.dumps(references_from_payload, indent=2, ensure_ascii=False)}")
        evaluation.references = references_from_payload if references_from_payload is not None else evaluation.references        
        micronutrients_to_set = {k: v_data[f"mic_{k.split('_')[0]}"] for k in ['potassium_mg', 'calcium_mg', 'sodium_mg', 'cholesterol_mg'] if v_data.get(f"mic_{k.split('_')[0]}") is not None}
        evaluation.set_micronutrients(micronutrients_to_set if micronutrients_to_set else evaluation.get_micronutrients())
        evaluation.set_base_foods(plan_data.get('base_foods', evaluation.get_base_foods()))
        evaluation.consultation_date = datetime.now(timezone.utc)
        evaluation.user_id = current_user.id # Re-asegurar la propiedad

        db.session.commit()

        selected_favorite_recipes_titles = data.get('selected_favorite_recipes', [])
        full_plan_text_for_favorites = edited_plan_text

        if selected_favorite_recipes_titles and full_plan_text_for_favorites:
            app.logger.info(f"ACTUALIZAR_EVALUACION: Procesando {len(selected_favorite_recipes_titles)} recetas favoritas seleccionadas.")

            # Determinar el tag basado en el tipo de dieta del plan
            tag_from_diet_type = None
            other_diet_text = plan_data.get('other_diet_type_text', '').strip()
            if other_diet_text:
                tag_from_diet_type = other_diet_text.lower().strip().replace(" ", "_") # Normalizar
            elif plan_data.get('diet_type'):
                tag_from_diet_type = plan_data.get('diet_type').lower().strip().replace(" ", "_") # Normalizar

            for recipe_title_from_frontend in selected_favorite_recipes_titles:
                match_num_part = re.search(r"(N°\d+)", recipe_title_from_frontend, re.IGNORECASE)
                recipe_identifier_for_parse = None

                if match_num_part:
                    recipe_identifier_for_parse = match_num_part.group(1).strip()
                    app.logger.debug(f"FAV_SAVE (ActualizarEval): Identificador NUMÉRICO extraído: '{recipe_identifier_for_parse}' de '{recipe_title_from_frontend}'")
                else: 
                    match_title_only = re.match(r"Receta\s*(?:N°|No\.|N\.)?\s*\d*:\s*(.+)", recipe_title_from_frontend, re.IGNORECASE)
                    if match_title_only:
                        recipe_identifier_for_parse = match_title_only.group(1).strip().rstrip('.').strip()
                    else:
                        recipe_identifier_for_parse = recipe_title_from_frontend.strip().rstrip('.').strip()
                    app.logger.warning(f"FAV_SAVE (ActualizarEval): No se pudo extraer 'N°X' de '{recipe_title_from_frontend}'. Usando nombre: '{recipe_identifier_for_parse}' para parseo.")

                app.logger.debug(f"FAV_SAVE (ActualizarEval): Identificador FINAL para parseo: '{recipe_identifier_for_parse}'")
                parsed_recipe_data = parse_recipe_from_text(recipe_identifier_for_parse, full_plan_text_for_favorites)

                if parsed_recipe_data:
                    actual_recipe_name_from_recetario_log = parsed_recipe_data.get('name', 'Nombre no encontrado en parseo')
                    app.logger.debug(f"FAV_SAVE (ActualizarEval): Receta con identificador '{recipe_identifier_for_parse}' (Nombre real: '{actual_recipe_name_from_recetario_log}') PARSEADA. "
                                     f"Ingredientes: {bool(parsed_recipe_data.get('ingredients'))}, "
                                     f"Num_servings: {parsed_recipe_data.get('num_servings')}")
                    if not parsed_recipe_data.get('ingredients'):
                        app.logger.warning(f"FAV_SAVE (ActualizarEval): Receta con identificador '{recipe_identifier_for_parse}' (Nombre real: '{actual_recipe_name_from_recetario_log}') parseada PERO SIN INGREDIENTES. No se guardará.")
                else:
                    app.logger.warning(f"FAV_SAVE (ActualizarEval): Falló el parseo de la receta con identificador '{recipe_identifier_for_parse}' (de: '{recipe_title_from_frontend}'). No se guardará.")

                if parsed_recipe_data and parsed_recipe_data.get('ingredients'): 
                    actual_recipe_name_from_recetario = parsed_recipe_data.get('name')
                    if not actual_recipe_name_from_recetario:
                        app.logger.error(f"FAV_SAVE (ActualizarEval): 'parsed_recipe_data' no tiene 'name' para identificador '{recipe_identifier_for_parse}'. Saltando guardado.")
                        continue
                    try:
                        existing_fav = UserPreparation.query.filter_by(name=actual_recipe_name_from_recetario).first()
                        if existing_fav:
                            app.logger.info(f"ACTUALIZAR_EVALUACION: Receta favorita '{existing_fav.name}' ya existe. Omitiendo duplicado.")
                            continue
                        
                        servings_value_from_parse_fav_update = parsed_recipe_data.get('num_servings', 1.0)
                        try:
                            num_servings_for_prep_fav_update = float(servings_value_from_parse_fav_update)
                        except (ValueError, TypeError): 
                            app.logger.warning(f"FAV_SAVE (ActualizarEval - Favorita): num_servings '{servings_value_from_parse_fav_update}' no es un float válido. Usando 1.0.")
                            num_servings_for_prep_fav_update = 1.0
                            
                        new_preparation = UserPreparation(
                            name=actual_recipe_name_from_recetario,
                            description=parsed_recipe_data.get('description', "Receta generada por IA y marcada como favorita."),
                            instructions=parsed_recipe_data.get('instructions', "Instrucciones no parseadas."),
                            preparation_type=parsed_recipe_data.get('preparation_type', 'almuerzo'), 
                            num_servings=num_servings_for_prep_fav_update,
                            source='favorita_ia_finalizar'
                        )
                        
                        # Asignar el tag basado en el tipo de dieta del plan
                        if tag_from_diet_type:
                            new_preparation.set_suitability_tags([tag_from_diet_type])
                            app.logger.info(f"FAV_SAVE (ActualizarEval): Asignando tag '{tag_from_diet_type}' a la preparación '{new_preparation.name}'.")
                        else:
                            new_preparation.set_suitability_tags([])
                            app.logger.info(f"FAV_SAVE (ActualizarEval): No se asignó tag de tipo de dieta a '{new_preparation.name}' (tipo de dieta del plan no especificado o 'Otra' sin texto).")

                        db_ready_ingredients_update = []
                        ingredients_for_calc_update = parsed_recipe_data.get('ingredients', [])

                        for ing_from_parse_update in ingredients_for_calc_update:
                            db_ready_ingredients_update.append({
                                'original_description': ing_from_parse_update.get('original_line', ing_from_parse_update.get('item', '')),
                                'parsed_item_name': ing_from_parse_update.get('item', ''),
                                'quantity': ing_from_parse_update.get('quantity', 0.0),
                                'unit': ing_from_parse_update.get('unit', 'N/A')
                            })
                        new_preparation.set_ingredients(db_ready_ingredients_update)
                        
                        total_nutri_fav_update = calculate_total_nutritional_info(ingredients_for_calc_update)
                        
                        num_servings_fav_update = new_preparation.num_servings 
                        if not (isinstance(num_servings_fav_update, (int, float)) and num_servings_fav_update > 0):
                            app.logger.warning(f"FAV_SAVE (ActualizarEval - Favorita): num_servings_fav_update ({num_servings_fav_update}) no es un número positivo para receta '{new_preparation.name}'. Usando 1.0 para cálculo.")
                            num_servings_fav_update = 1.0
                        
                        new_preparation.calories_per_serving = round(total_nutri_fav_update['calories'] / num_servings_fav_update, 2) if num_servings_fav_update != 0 else 0.0
                        new_preparation.protein_g_per_serving = round(total_nutri_fav_update['protein_g'] / num_servings_fav_update, 2) if num_servings_fav_update != 0 else 0.0
                        new_preparation.carb_g_per_serving = round(total_nutri_fav_update['carb_g'] / num_servings_fav_update, 2) if num_servings_fav_update != 0 else 0.0
                        new_preparation.fat_g_per_serving = round(total_nutri_fav_update['fat_g'] / num_servings_fav_update, 2) if num_servings_fav_update != 0 else 0.0
                        new_preparation.set_micronutrients_per_serving(total_nutri_fav_update['micros'])

                        db.session.add(new_preparation)
                        app.logger.info(f"FAV_SAVE (ActualizarEval): Receta '{new_preparation.name}' preparada para guardar como favorita.")
                    except Exception as fav_save_e:
                        app.logger.error(f"FAV_SAVE (ActualizarEval): Error al procesar la receta favorita '{actual_recipe_name_from_recetario}': {fav_save_e}", exc_info=True)

        # Commit general después de procesar todas las favoritas y la evaluación principal
        try:
            db.session.commit() # Esto guardará la evaluación actualizada y cualquier nueva preparación favorita.
            app.logger.info("ACTUALIZAR_EVALUACION: Commit final realizado exitosamente.")
        except Exception as final_commit_e_update:
            db.session.rollback()
            app.logger.error(f"ACTUALIZAR_EVALUACION: Error en el commit final: {final_commit_e_update}", exc_info=True)
            flash('Error crítico al guardar todos los datos actualizados. Algunos cambios podrían no haberse guardado.', 'danger')

        app.logger.info(f"ACTUALIZAR_EVALUACION: Regenerando PDF técnico para Evaluación ID: {evaluation_id} después de la actualización.")
        nombre_pdf_tecnico_actualizado = f"EvaluacionNutricional_COMPLETA_{patient.surname}_{patient.cedula}_{evaluation.consultation_date.strftime('%Y%m%d_%H%M')}.pdf"
        pdf_buffer_tecnico_actualizado = crear_pdf_v2(evaluation)

        if pdf_buffer_tecnico_actualizado.getbuffer().nbytes > 0:
            nuevo_drive_file_id = subir_a_drive_v2(pdf_buffer_tecnico_actualizado, nombre_pdf_tecnico_actualizado)
            if nuevo_drive_file_id:
                if evaluation.pdf_storage_path:
                    app.logger.info(f"ACTUALIZAR_EVALUACION: Guardando PDF anterior ID '{evaluation.pdf_storage_path}' en historial.")
                    evaluation.add_historical_pdf_id(evaluation.pdf_storage_path)
                
                evaluation.pdf_storage_path = nuevo_drive_file_id
                
                db.session.commit()
                app.logger.info(f"ACTUALIZAR_EVALUACION: PDF técnico regenerado y subido a Drive. Nuevo Drive ID: {nuevo_drive_file_id}")
            else:
                app.logger.warning(f"ACTUALIZAR_EVALUACION: PDF técnico regenerado, pero falló la subida a Drive para Evaluación ID: {evaluation_id}.")
                flash('Advertencia: La evaluación se actualizó, pero no se pudo re-subir el PDF técnico a Google Drive.', 'warning')
        else:
            app.logger.error(f"ACTUALIZAR_EVALUACION: Falló la regeneración del PDF técnico (buffer vacío) para Evaluación ID: {evaluation_id}.")

        app.logger.info(f"Evaluación ID {evaluation_id} actualizada exitosamente.")
        return jsonify({'message': f'Evaluación ID {evaluation_id} actualizada correctamente.', 'patient_id': patient.id, 'evaluation_id': evaluation.id})

    except json.JSONDecodeError as json_err:
        app.logger.error(f"Error de decodificación JSON en /actualizar_evaluacion/{evaluation_id}: {json_err}", exc_info=True)
        return jsonify({'error': f'Error en el formato JSON de la solicitud: {json_err}'}), 400
    except ValueError as e:
        app.logger.error(f"Error de valor en /actualizar_evaluacion/{evaluation_id}: {e}", exc_info=True)
        return jsonify({'error': f'Error en formato de dato: {e}'}), 400
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error al actualizar Evaluación ID {evaluation_id}: {e}", exc_info=True)
        return jsonify({'error': 'Error inesperado al actualizar la evaluación.'}), 500

@app.route('/ver_pdf/<int:evaluation_id>')
def ver_pdf(evaluation_id: int):
    app.logger.info(f"Solicitud para ver PDF de Evaluación ID: {evaluation_id}")
    evaluation = Evaluation.query.get(evaluation_id) 
    if not evaluation:
        abort(404, description="Evaluación no encontrada")

    nombre_archivo_base = f"EvaluacionNutricional_COMPLETA_{evaluation.patient.surname}_{evaluation.patient.cedula}_{evaluation.consultation_date.strftime('%Y%m%d_%H%M')}.pdf"

    if evaluation.pdf_storage_path:
        app.logger.info(f"Intentando descargar PDF almacenado (Drive ID: {evaluation.pdf_storage_path}) para Evaluación ID: {evaluation.id}")
        pdf_buffer = download_from_drive(evaluation.pdf_storage_path)
        
        if pdf_buffer and pdf_buffer.getbuffer().nbytes > 0:
            app.logger.info(f"PDF almacenado descargado exitosamente para Evaluación ID: {evaluation.id}")
            pdf_buffer.seek(0)
            return send_file(pdf_buffer, mimetype='application/pdf', download_name=nombre_archivo_base, as_attachment=False)
        else:
            app.logger.warning(f"No se pudo descargar el PDF almacenado (ID: {evaluation.pdf_storage_path}) para Evaluación ID {evaluation.id}. El archivo podría no existir en Drive o hubo un error.")
            flash("No se pudo recuperar el PDF almacenado. Puede que haya sido eliminado de Google Drive o hubo un error al descargarlo. Se intentará regenerar.", "warning")
            # Fallback a regenerar si la descarga falla

    # Si no hay pdf_storage_path o la descarga falló, se regenera.
    app.logger.warning(f"Regenerando PDF para Evaluación ID {evaluation.id} (ya sea porque no hay path o la descarga falló).")
    try: 
        app.logger.info(f"Generando PDF para Evaluación ID: {evaluation.id} (Paciente: {evaluation.patient.surname})")
        pdf_buffer_regenerado = crear_pdf_v2(evaluation) 
        if not pdf_buffer_regenerado.getbuffer().nbytes > 0:
             app.logger.error(f"Regeneración de PDF falló (buffer vacío) para Evaluación ID {evaluation.id}.")
             abort(500, description="Error al generar contenido PDF")

        pdf_buffer_regenerado.seek(0)
        # Opcional: añadir un sufijo al nombre si es regenerado y había un path
        # nombre_archivo_display = nombre_archivo_base if not evaluation.pdf_storage_path else nombre_archivo_base.replace(".pdf", "_regenerado.pdf")
        return send_file(pdf_buffer_regenerado, mimetype='application/pdf', download_name=nombre_archivo_base, as_attachment=False)
    except Exception as e: 
        app.logger.error(f"Error al regenerar/enviar PDF para Evaluación ID {evaluation_id}: {e}", exc_info=True)
        abort(500, description="Error al generar PDF.")

@app.route('/ver_pdf_paciente/<int:evaluation_id>')
def ver_pdf_paciente(evaluation_id: int):
    app.logger.info(f"Solicitud para ver PDF de PACIENTE de Evaluación ID: {evaluation_id}")
    evaluation = Evaluation.query.get_or_404(evaluation_id)

    try:
        # Para el PDF del paciente, generalmente se regenera ya que su contenido puede ser
        # un subconjunto o tener un formato diferente al PDF completo del nutricionista.
        # Si se decidiera almacenar también el PDF del paciente, se seguiría una lógica similar a /ver_pdf.
        app.logger.info(f"Generando PDF de PACIENTE para Evaluación ID: {evaluation.id} (Paciente: {evaluation.patient.surname})")
        pdf_buffer = crear_pdf_paciente(evaluation)
        if not pdf_buffer.getbuffer().nbytes > 0:
             app.logger.error(f"Error: El buffer del PDF del paciente para Evaluación ID {evaluation_id} está vacío.")
             abort(500, description="Error al generar contenido del PDF para el paciente.")

        pdf_buffer.seek(0)
        nombre_archivo = f"PlanNutricional_{evaluation.patient.surname}_{evaluation.patient.cedula}_{evaluation.consultation_date.strftime('%Y%m%d')}.pdf"

        return send_file(pdf_buffer, mimetype='application/pdf', download_name=nombre_archivo, as_attachment=False)
    except Exception as e:
        app.logger.error(f"Error al generar/enviar PDF de PACIENTE para Evaluación ID {evaluation_id}: {e}", exc_info=True)        
        abort(500, description="Error al generar PDF para el paciente.")

@app.route('/enviar_plan_por_email/<int:evaluation_id>', methods=['POST'])
# @login_required # Descomentar si se implementa login
def enviar_plan_email_route(evaluation_id):
    app.logger.info(f"Recibida solicitud para enviar email para Evaluación ID: {evaluation_id}")
    evaluation = Evaluation.query.get_or_404(evaluation_id)
    patient = evaluation.patient

    if not patient:
        app.logger.error(f"Evaluación ID {evaluation_id} no tiene paciente asociado.")
        return jsonify({'error': 'Evaluación sin paciente asociado.'}), 500

    if not patient.email:
        app.logger.warning(f"Intento de enviar email para Evaluación ID {evaluation_id}, pero Paciente ID {patient.id} ({patient.name} {patient.surname}) no tiene email registrado.")
        return jsonify({'error': 'El paciente no tiene una dirección de email registrada.'}), 400

    try:
        app.logger.info(f"Enviando email con plan a {patient.email} para Evaluación ID {evaluation_id}.")
        nombre_pdf_paciente = f"PlanNutricional_{patient.surname}_{patient.cedula}_{evaluation.consultation_date.strftime('%Y%m%d')}.pdf"
        pdf_buffer_paciente = crear_pdf_paciente(evaluation)

        if not pdf_buffer_paciente or pdf_buffer_paciente.getbuffer().nbytes == 0:
            app.logger.error(f"No se pudo generar el PDF del paciente para Evaluación ID {evaluation_id} al intentar enviar email.")
            return jsonify({'error': 'No se pudo generar el PDF del paciente para el envío.'}), 500

        asunto = f"Tu Plan Nutricional Personalizado de NutriApp - {patient.name} {patient.surname}"
        cuerpo_html_email = f"""
        <p>Hola {patient.name},</p><br>
        <p>Adjunto encontrarás tu plan de alimentación personalizado correspondiente a la evaluación del {evaluation.consultation_date.strftime('%d/%m/%Y')}.</p>
        <p>Si tienes alguna pregunta, no dudes en contactarme.</p><br/>
        <p>Saludos cordiales,</p>
        <p><strong>Tu Nutricionista - NutriApp</strong></p>
        """
        email_enviado = enviar_email_con_adjunto(patient.email, asunto, cuerpo_html_email, pdf_buffer_paciente, nombre_pdf_paciente)

        if email_enviado:
            app.logger.info(f"Email con plan enviado exitosamente a {patient.email} para Evaluación ID {evaluation_id}.")
            return jsonify({'message': f'Plan enviado por email a {patient.email} exitosamente.'}), 200
        else: 
            app.logger.error(f"Falló el envío de email a {patient.email} para Evaluación ID {evaluation_id} (la función de envío devolvió False).")
            return jsonify({'error': 'No se pudo enviar el email con el plan al paciente (falló la función de envío).'}), 500
    # El bloque 'except' debe estar alineado con el 'try'
    except Exception as e: 
        app.logger.error(f"Error INESPERADO al intentar enviar email para Evaluación ID {evaluation_id}: {e}", exc_info=True)
        return jsonify({'error': 'Error interno del servidor al procesar el envío de email.'}), 500



# --- Rutas API para la App del Paciente ---

# --- Rutas API para la App del Paciente ---

@app.route('/api/patient/<int:patient_id>/weight', methods=['GET', 'POST'])
@patient_auth_required
def patient_weight_api(patient_id):
    patient = g.patient # El paciente ya fue verificado y cargado por el decorador

    if request.method == 'GET':
        # Obtener el historial de peso del paciente
        weight_entries = patient.weight_history.order_by(WeightEntry.date.asc()).all()
        entries_data = [{'date': entry.date.strftime('%Y-%m-%d'), 'weight_kg': entry.weight_kg, 'notes': entry.notes} for entry in weight_entries]
        app.logger.info(f"API /patient/{patient_id}/weight - Obtenido historial de peso. {len(entries_data)} entradas.")
        return jsonify({'entries': entries_data})

    elif request.method == 'POST':
        # Registrar un nuevo peso para el paciente
        data = request.get_json()
        if not data or not all(key in data for key in ['date', 'weight_kg']):
            return jsonify({'error': 'Faltan datos (fecha o peso).'}), 400

        try:
            weight_kg = float(data['weight_kg'])
            entry_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
            notes = data.get('notes', '')

            new_entry = WeightEntry(patient_id=patient.id, date=entry_date, weight_kg=weight_kg, notes=notes)
            db.session.add(new_entry)
            db.session.commit()
            app.logger.info(f"API /patient/{patient_id}/weight - Nuevo peso registrado: {weight_kg} kg en {entry_date}.")
            return jsonify({'message': 'Peso registrado exitosamente.'}), 201
        except ValueError as ve:
            app.logger.warning(f"API /patient/{patient_id}/weight - Error de formato: {ve}")
            return jsonify({'error': f'Error en el formato de los datos: {ve}'}), 400
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"API /patient/{patient_id}/weight - Error al guardar peso: {e}", exc_info=True)
            return jsonify({'error': 'Error al registrar el peso.'}), 500
    else:
        return jsonify({'error': 'Método no permitido.'}), 405

@app.route('/api/patient/me/chat/messages', methods=['GET'])
@patient_auth_required
def get_my_chat_messages():
    patient = g.patient # El paciente ya fue verificado y cargado por el decorador
    messages = patient.chat_messages.order_by(ChatMessage.timestamp.asc()).all()
    messages_data = [msg.to_dict() for msg in messages]
    app.logger.info(f"API Paciente Chat: Obtenidos {len(messages_data)} mensajes para Paciente ID {patient.id}")
    return jsonify({'messages': messages_data})

@app.route('/api/patient/me/chat/messages', methods=['POST'])
@patient_auth_required
def send_my_chat_message():
    patient = g.patient
    data = request.get_json()
    if not data or not data.get('content'):
        return jsonify({'error': 'El contenido del mensaje no puede estar vacío.'}), 400
    
    message_text = data['content'].strip()
    if not message_text:
        return jsonify({'error': 'El contenido del mensaje no puede estar vacío.'}), 400

    try:
        new_message = ChatMessage(
            patient_id=patient.id,
            sender_is_patient=True, # Paciente envía
            message_text=message_text
        )
        db.session.add(new_message)
        db.session.commit()
        app.logger.info(f"API Paciente Chat: Nuevo mensaje del Paciente ID {patient.id} guardado.")
        return jsonify(new_message.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"API Paciente Chat: Error al guardar mensaje para Paciente ID {patient.id}: {e}", exc_info=True)
        return jsonify({'error': 'Error interno al guardar el mensaje.'}), 500

# --- API para el Chat del Nutricionista ---

@app.route('/api/nutricionista/chat/<int:patient_id>/messages', methods=['GET'])
@login_required
def get_nutricionista_chat_messages(patient_id):
    # Verify the nutritionist owns this patient
    patient = Patient.query.filter_by(id=patient_id, user_id=current_user.id).first_or_404()
    
    messages = patient.chat_messages.order_by(ChatMessage.timestamp.asc()).all()
    messages_data = [msg.to_dict() for msg in messages]
    
    app.logger.info(f"API Nutri Chat: Obtenidos {len(messages_data)} mensajes para Paciente ID {patient_id}")
    return jsonify({'messages': messages_data})

@app.route('/api/nutricionista/chat/<int:patient_id>/messages', methods=['POST'])
@login_required
def send_nutricionista_chat_message(patient_id):
    patient = Patient.query.filter_by(id=patient_id, user_id=current_user.id).first_or_404()
    data = request.get_json()
    
    if not data or not data.get('content'):
        return jsonify({'error': 'El contenido del mensaje no puede estar vacío.'}), 400
        
    message_text = data['content'].strip()
    if not message_text:
        return jsonify({'error': 'El contenido del mensaje no puede estar vacío.'}), 400

    try:
        new_message = ChatMessage(
            patient_id=patient.id,
            sender_is_patient=False, # El nutricionista está enviando
            message_text=message_text
        )
        db.session.add(new_message)
        db.session.commit()
        app.logger.info(f"API Nutri Chat: Nuevo mensaje del nutricionista (ID: {current_user.id}) al Paciente ID {patient_id} guardado.")
        
        return jsonify(new_message.to_dict()), 201

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"API Nutri Chat: Error al guardar mensaje para Paciente ID {patient_id}: {e}", exc_info=True)
        return jsonify({'error': 'Error interno al guardar el mensaje.'}), 500

# --- RUTAS EXISTENTES (Nutricionista) ---

# (estas rutas ya las tienes, colócalas DESPUÉS de las nuevas rutas y APIS del paciente)

@app.route('/pacientes', methods=['GET'])
def api_listar_pacientes(): # Esta es para el autocompletado del nutricionista
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'pacientes': []})

    search_term = f"%{q}%"
    coincidentes = Patient.query.filter(Patient.cedula.ilike(search_term) | Patient.name.ilike(search_term) | Patient.surname.ilike(search_term)).order_by(Patient.surname, Patient.name).limit(20).all()

    resultado = [
        {'id': p.id, 'nombre': p.name, 'apellido': p.surname, 'cedula': p.cedula}
        for p in coincidentes
    ]
    app.logger.info(f"API /pacientes con query '{q}' devolvió {len(resultado)} resultados.")
    return jsonify({'pacientes': resultado}) # type: ignore

@app.route('/paciente/<int:patient_id>/historial', methods=['GET'])
@login_required
def historial_paciente(patient_id):
    app.logger.info(f"Accediendo al historial del paciente ID: {patient_id}")
    patient = Patient.query.filter_by(id=patient_id, user_id=current_user.id).first_or_404()
    # Ordenar evaluaciones por fecha ascendente para los gráficos
    evaluations = patient.evaluations.order_by(Evaluation.consultation_date.asc()).all() 

    app.logger.info(f"Paciente: {patient.name} {patient.surname}, Evaluaciones encontradas: {len(evaluations)}")

    # Preparar datos para gráficos
    chart_labels = []
    weight_data = []
    imc_data = []
    waist_hip_ratio_data = []
    waist_height_ratio_data = []

    for ev in evaluations: # Iterar sobre las evaluaciones ya ordenadas
        chart_labels.append(ev.consultation_date.strftime('%d/%m/%Y'))
        weight_data.append(ev.weight_at_eval if ev.weight_at_eval is not None else None)
        imc_data.append(ev.calculated_imc if ev.calculated_imc is not None else None)
        waist_hip_ratio_data.append(ev.calculated_waist_hip_ratio if ev.calculated_waist_hip_ratio is not None else None)
        waist_height_ratio_data.append(ev.calculated_waist_height_ratio if ev.calculated_waist_height_ratio is not None else None)
    
    # Obtener los rangos de referencia (usaremos los de la última evaluación si existen,
    # o podrías tener rangos fijos definidos en config.py si son universales)
    last_evaluation_with_references = next((ev for ev in reversed(evaluations) if ev.references_json and ev.references_json != '{}'), None)
    reference_ranges = {}
    if last_evaluation_with_references:
        # Asegurarse de que references_json sea un string antes de intentar json.loads
        if isinstance(last_evaluation_with_references.references_json, str):
            try:
                reference_ranges = json.loads(last_evaluation_with_references.references_json)
            except json.JSONDecodeError:
                app.logger.warning(f"Error al decodificar references_json para la evaluación ID {last_evaluation_with_references.id}: {last_evaluation_with_references.references_json}")
                reference_ranges = {} # Mantener vacío si hay error
        else:
            app.logger.warning(f"references_json para la evaluación ID {last_evaluation_with_references.id} no es un string, es {type(last_evaluation_with_references.references_json)}. Se usará {{}}.")
            reference_ranges = {}
    
    chart_data = {
        'labels': chart_labels,
        'weight': weight_data,
        'imc': imc_data,
        'whr': waist_hip_ratio_data, 
        'whtr': waist_height_ratio_data,
        'references': reference_ranges # Añadir los rangos de referencia
    }

    return render_template('historial_paciente.html', 
                           patient=patient, 
                           evaluations=evaluations, # Se pasa la lista ordenada ascendentemente para los gráficos, la plantilla la reordena para la lista visual
                           chart_data=chart_data,
                           education_levels=app.config.get('EDUCATION_LEVELS', []),
                           purchasing_power_levels=app.config.get('PURCHASING_POWER_LEVELS', []))

@app.route('/nutricionista_chat/<int:patient_id>')
@login_required
def nutricionista_chat_view(patient_id):
    """Vista de chat para el nutricionista con un paciente específico."""
    patient = Patient.query.filter_by(id=patient_id, user_id=current_user.id).first_or_404()
    return render_template('nutricionista_chat.html', patient=patient)

@app.route('/get_all_patients', methods=['GET'])
@login_required
def get_all_patients():
    """Devuelve una lista de todos los pacientes del usuario actual."""
    try:
        patients = Patient.query.filter_by(user_id=current_user.id).order_by(Patient.surname, Patient.name).all()
        patients_data = [{
            "id": p.id, "name": p.name, "surname": p.surname, "cedula": p.cedula
        } for p in patients]
        return jsonify({"results": patients_data})
    except Exception as e:
        app.logger.error(f"Error al obtener todos los pacientes: {e}")
        return jsonify({"error": "Error al obtener la lista de pacientes"}), 500

@app.route('/check_email_availability', methods=['POST'])
def check_email_availability():
    data = request.get_json()
    email_to_check = data.get('email', '').strip()
    current_patient_id_str = data.get('current_patient_id', '') # ID del paciente que se está editando (si aplica)

    if not email_to_check:
        return jsonify({'available': True, 'message': ''}) # No hay email para verificar

    query = Patient.query.filter(Patient.email == email_to_check)
    
    # Si se está editando un paciente, excluirlo de la búsqueda de duplicados
    current_patient_id = None
    if current_patient_id_str:
        try:
            current_patient_id = int(current_patient_id_str)
            query = query.filter(Patient.id != current_patient_id)
        except ValueError:
            app.logger.warning(f"ID de paciente actual inválido recibido en check_email: {current_patient_id_str}")

    existing_patient = query.first()

    if existing_patient:
        return jsonify({'available': False, 'message': f'Email ya registrado para: {existing_patient.name} {existing_patient.surname} (CI: {existing_patient.cedula})'})
    else:
        return jsonify({'available': True, 'message': 'Email disponible'})

@app.route('/paciente/<int:patient_id>/editar', methods=['GET', 'POST'])
def editar_paciente(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            surname = request.form.get('surname', '').strip()
            email = request.form.get('email', '').strip()
            phone_number = request.form.get('phone_number', '').strip()
            sex = request.form.get('sex', '').strip() # Limpiar espacios
            education_level = request.form.get('education_level')
            purchasing_power = request.form.get('purchasing_power')
            height_cm_str = request.form.get('height_cm')
            dob_str = request.form.get('dob')

            if not name or len(name) < 2:
                flash('El nombre es requerido y debe tener al menos 2 caracteres.', 'danger')
                return render_template('editar_paciente.html', patient=patient, education_levels=app.config['EDUCATION_LEVELS'], purchasing_power_levels=app.config['PURCHASING_POWER_LEVELS'])
            if not surname or len(surname) < 2:
                flash('El apellido es requerido y debe tener al menos 2 caracteres.', 'danger')
                return render_template('editar_paciente.html', patient=patient, education_levels=app.config['EDUCATION_LEVELS'], purchasing_power_levels=app.config['PURCHASING_POWER_LEVELS'])
            if email and not re.match(r"[^@]+@[^@]+\.[^@]+", email):
                flash('Formato de email no válido.', 'danger')
                return render_template('editar_paciente.html', patient=patient, education_levels=app.config['EDUCATION_LEVELS'], purchasing_power_levels=app.config['PURCHASING_POWER_LEVELS'])
            if sex and sex not in ['masculino', 'femenino', 'otro']:
                flash('Sexo no válido.', 'danger')
                return render_template('editar_paciente.html', patient=patient, education_levels=app.config['EDUCATION_LEVELS'], purchasing_power_levels=app.config['PURCHASING_POWER_LEVELS'])
            if education_level and education_level not in [val[0] for val in app.config['EDUCATION_LEVELS']]:
                flash('Nivel educativo no válido.', 'danger')
                return render_template('editar_paciente.html', patient=patient, education_levels=app.config['EDUCATION_LEVELS'], purchasing_power_levels=app.config['PURCHASING_POWER_LEVELS'])
            if purchasing_power and purchasing_power not in [val[0] for val in app.config['PURCHASING_POWER_LEVELS']]:
                flash('Nivel de poder adquisitivo no válido.', 'danger')
                return render_template('editar_paciente.html', patient=patient, education_levels=app.config['EDUCATION_LEVELS'], purchasing_power_levels=app.config['PURCHASING_POWER_LEVELS'])

            patient.name = name
            patient.surname = surname
            # La cédula generalmente no se edita, pero si se permite, asegurar unicidad
            # patient.cedula = request.form.get('cedula') 
            if dob_str:
                patient.dob = datetime.strptime(dob_str, '%Y-%m-%d').date()
            else:
                patient.dob = None
            patient.sex = sex

            # --- Check for email uniqueness before assigning ---
            if email and email != patient.email:
                existing_patient_with_email = Patient.query.filter(Patient.email == email, Patient.id != patient_id).first()
                if existing_patient_with_email:
                    flash(f'El email "{email}" ya está en uso por otro paciente. Por favor, use uno diferente.', 'danger')
                    return render_template('editar_paciente.html', patient=patient, education_levels=app.config['EDUCATION_LEVELS'], purchasing_power_levels=app.config['PURCHASING_POWER_LEVELS'])
            patient.email = email or None
            patient.phone_number = phone_number or None
            patient.education_level = education_level or None
            patient.purchasing_power = purchasing_power or None
            
            cleaned_height_cm_str = height_cm_str.strip() if height_cm_str else None
            if cleaned_height_cm_str:
                try:
                    h_val = float(cleaned_height_cm_str)
                    if not (30 < h_val < 300):
                        flash('Altura no válida (debe estar entre 30 y 300 cm).', 'danger')
                        return render_template('editar_paciente.html', patient=patient, education_levels=app.config['EDUCATION_LEVELS'], purchasing_power_levels=app.config['PURCHASING_POWER_LEVELS'])
                    patient.height_cm = h_val
                except ValueError:
                    flash('Altura debe ser un número.', 'danger')
                    return render_template('editar_paciente.html', patient=patient, education_levels=app.config['EDUCATION_LEVELS'], purchasing_power_levels=app.config['PURCHASING_POWER_LEVELS'])
            else:
                patient.height_cm = None

            patient.set_allergies(request.form.get('allergies', ''))
            patient.set_intolerances(request.form.get('intolerances', ''))
            patient.set_preferences(request.form.get('preferences', ''))
            patient.set_aversions(request.form.get('aversions', ''))

            db.session.commit()
            flash('Datos del paciente actualizados correctamente.', 'success')
            app.logger.info(f"Datos del paciente ID {patient.id} actualizados.")
            return redirect(url_for('historial_paciente', patient_id=patient.id))
        except ValueError as ve:
            db.session.rollback()
            flash(f'Error en el formato de los datos: {ve}', 'danger')
            app.logger.error(f"Error de formato al editar paciente ID {patient.id}: {ve}")
        except Exception as e:
            db.session.rollback()
            flash('Ocurrió un error al actualizar los datos del paciente.', 'danger')
            app.logger.error(f"Error al editar paciente ID {patient.id}: {e}", exc_info=True)

    return render_template('editar_paciente.html', patient=patient,
                           education_levels=app.config['EDUCATION_LEVELS'],
                           purchasing_power_levels=app.config['PURCHASING_POWER_LEVELS'])

@app.route('/evaluacion/<int:evaluation_id>/editar', methods=['GET', 'POST'])
def editar_evaluacion_form(evaluation_id): # Renamed to avoid conflict, will only handle GET
    evaluation = Evaluation.query.get_or_404(evaluation_id)
    patient = evaluation.patient
    if not patient:
        app.logger.error(f"Error crítico: Evaluación ID {evaluation_id} no tiene paciente asociado.")
        flash("Error: La evaluación no está asociada a ningún paciente.", "danger") # Should not happen with get_or_404
        return redirect(url_for('pacientes_dashboard'))

    # Método GET: Mostrar el formulario con los datos de la evaluación
    app.logger.info(f"Accediendo al formulario para EDITAR Evaluación ID: {evaluation_id}")
    
    # Preparar los datos de la evaluación para pasar al template
    # (similar a lo que hace get_evaluation_data)
    evaluation_data_for_form = {
        'patient_id': patient.id,
        'name': patient.name, 'surname': patient.surname, 'cedula': patient.cedula,
        'email': patient.email,
        'phone_number': patient.phone_number,
        'education_level': patient.education_level,
        'purchasing_power': patient.purchasing_power,
        'dob': patient.dob.strftime('%Y-%m-%d') if patient.dob else None, 'sex': patient.sex,
        'height_cm': patient.height_cm, 
        'allergies': patient.get_allergies(), 'intolerances': patient.get_intolerances(),
        'preferences': patient.get_preferences(), 'aversions': patient.get_aversions(),
 
        'evaluation_id': evaluation.id, 
        'consultation_date': evaluation.consultation_date.isoformat(), 
        'weight_at_eval': evaluation.weight_at_eval,
        'wrist_circumference_cm': evaluation.wrist_circumference_cm,
        'waist_circumference_cm': evaluation.waist_circumference_cm,
        'hip_circumference_cm': evaluation.hip_circumference_cm,
        'gestational_age_weeks': evaluation.gestational_age_weeks,
        'activity_factor': evaluation.activity_factor,
        'pathologies': evaluation.get_pathologies(), 
        'other_pathologies_text': evaluation.other_pathologies_text,
        'postoperative_text': evaluation.postoperative_text,
        'diet_type': evaluation.diet_type,
        'other_diet_type_text': evaluation.other_diet_type_text,
        'target_weight': evaluation.target_weight,
        'target_waist_cm': evaluation.target_waist_cm,
        'target_protein_perc': evaluation.target_protein_perc,
        'target_carb_perc': evaluation.target_carb_perc,
        'target_fat_perc': evaluation.target_fat_perc,
        'edited_plan_text': evaluation.edited_plan_text, 
        'user_observations': evaluation.user_observations, 
        'micronutrients': evaluation.get_micronutrients(), 
        'base_foods': evaluation.get_base_foods(),
        'references': evaluation.references # Usar el property
    }

    all_ingredients = Ingredient.query.order_by(Ingredient.name).all()
    ingredients_for_js = [{'id': ing.id, 'name': ing.name} for ing in all_ingredients]

    return render_template('formulario_evaluacion.html',
                           action='edit_evaluation',
                           evaluation_data_to_load=evaluation_data_for_form,
                           activity_factors=app.config['ACTIVITY_FACTORS'],
                           available_pathologies=app.config['AVAILABLE_PATHOLOGIES'],
                           education_levels=app.config['EDUCATION_LEVELS'],
                           purchasing_power_levels=app.config['PURCHASING_POWER_LEVELS'],
                           diet_types=app.config['DIET_TYPES'],
                           all_ingredients=ingredients_for_js,
                           current_username=current_user.name or current_user.email,
                           current_date_str=evaluation.consultation_date.strftime('%d/%m/%Y')
                           )


# --- API para Preparaciones Favoritas ---


@app.route('/api/preparations', methods=['GET'])
# @login_required
def get_user_preparations():
    # user_id = current_user.id if hasattr(current_user, 'id') else None # Filtrar por usuario si hay login
    # preparations = UserPreparation.query.filter_by(user_id=user_id).order_by(UserPreparation.name).all()
    
    # Por ahora, listamos todas. Ajustar si se implementa user_id.
    preparations = UserPreparation.query.order_by(UserPreparation.name).all()
    app.logger.info(f"Listando {len(preparations)} preparaciones.")
    return jsonify([p.to_dict() for p in preparations]), 200

@app.route('/api/preparations/<int:preparation_id>', methods=['GET'])
# @login_required
def get_user_preparation(preparation_id):
    preparation = UserPreparation.query.get_or_404(preparation_id)
    # Aquí también podrías verificar si la preparación pertenece al usuario actual si tienes login
    app.logger.info(f"Obteniendo preparación ID: {preparation_id}")
    return jsonify(preparation.to_dict()), 200

@app.route('/api/preparations/<int:preparation_id>', methods=['DELETE'])
# @login_required
def delete_user_preparation(preparation_id):
    preparation = UserPreparation.query.get_or_404(preparation_id)
    # Verificar pertenencia al usuario si hay login
    
    try:
        db.session.delete(preparation)
        db.session.commit()
        app.logger.info(f"Preparación ID {preparation_id} eliminada.")
        return jsonify({'message': f'Preparación ID {preparation_id} eliminada exitosamente.'}), 200
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error al eliminar preparación ID {preparation_id}: {e}", exc_info=True)
        return jsonify({'error': 'Error interno al eliminar la preparación.'}), 500

@app.route('/api/favorite_generated_preparation', methods=['POST'])
# @login_required # Or remove if you don't have login implemented yet
def favorite_generated_preparation():
    data = request.get_json()
    if not data or 'recipe_name' not in data or 'full_plan_text' not in data:
        app.logger.error("API favorite_generated_preparation: Faltan datos 'recipe_name' o 'full_plan_text'.")
        return jsonify({'error': "Faltan datos: 'recipe_name' o 'full_plan_text' son requeridos."}), 400

    recipe_name_to_find = data['recipe_name']
    full_plan_text = data['full_plan_text']

    app.logger.info(f"API favorite_generated_preparation: Buscando receta '{recipe_name_to_find}' en el plan.")

    # Asumiendo que parse_recipe_from_text está definida en este archivo
    # y puede extraer los detalles de la receta del texto completo.
    parsed_recipe_data = parse_recipe_from_text(recipe_name_to_find, full_plan_text)

    if not parsed_recipe_data or not parsed_recipe_data.get('ingredients'):
        app.logger.error(f"API favorite_generated_preparation: No se pudo parsear la receta '{recipe_name_to_find}' del texto del plan.")
        return jsonify({'error': f"No se pudo parsear la receta '{recipe_name_to_find}' del texto del plan."}), 400

    try:
        user_id_for_prep = None
        # if hasattr(current_user, 'id') and current_user.is_authenticated:
        # user_id_for_prep = current_user.id
        # elif hasattr(g, 'user') and g.user and hasattr(g.user, 'id'): # Comprobación adicional para g.user.id
        # user_id_for_prep = g.user.id

        new_preparation = UserPreparation(
            # user_id=user_id_for_prep, # Temporarily remove or keep commented if User model/login is not ready
            name=parsed_recipe_data.get('name', recipe_name_to_find), # Usar el nombre parseado de la receta
            description=parsed_recipe_data.get('description', "Receta generada por IA y marcada como favorita."),
            instructions=parsed_recipe_data.get('instructions', "Instrucciones no parseadas."),
            preparation_type=parsed_recipe_data.get('preparation_type', 'almuerzo'),
            num_servings=float(parsed_recipe_data.get('num_servings', 1.0)),
            source='favorita_ia_plan'
        )
        new_preparation.set_ingredients(parsed_recipe_data.get('ingredients', []))

        db.session.add(new_preparation)
        db.session.commit()
        app.logger.info(f"API favorite_generated_preparation: Preparación '{new_preparation.name}' (ID: {new_preparation.id}) guardada como favorita.")
        return jsonify(new_preparation.to_dict()), 201 # 201 Created
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"API favorite_generated_preparation: Error al guardar la preparación favorita: {e}", exc_info=True)
        return jsonify({'error': f'Error interno al guardar la preparación favorita: {str(e)}'}), 500

@app.route('/mis_preparaciones')
@login_required
def mis_preparaciones_view():
    app.logger.info("Accediendo a la página 'Mis Preparaciones'")
    # Preparar los tags para el dropdown, usando el valor normalizado para el 'value' del option
    # y la descripción para el texto visible.
    available_suitability_tags_for_form = [
        {'value': dt[0].lower().strip().replace(" ", "_"), 'display': dt[1]}
        for dt in app.config.get('DIET_TYPES', []) if dt[0].lower() != 'otra' # Excluir 'Otra'
    ]
    app.logger.debug(f"Tags de adecuación para el formulario: {available_suitability_tags_for_form}")
    return render_template('mis_preparaciones.html', available_suitability_tags=available_suitability_tags_for_form)

@app.route('/api/preparations/<int:preparation_id>', methods=['PUT'])
# @login_required
def update_user_preparation(preparation_id):
    preparation = UserPreparation.query.get_or_404(preparation_id)
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No se recibieron datos para actualizar.'}), 400

    preparation.name = data.get('name', preparation.name)
    preparation.description = data.get('description', preparation.description)
    preparation.instructions = data.get('instructions', preparation.instructions)
    preparation.preparation_type = data.get('preparation_type', preparation.preparation_type)
    preparation.source = data.get('source', preparation.source)

    num_servings_from_data = data.get('num_servings')
    if num_servings_from_data is not None:
        try:
            num_servings_val = float(num_servings_from_data)
            preparation.num_servings = max(1.0, num_servings_val)
        except (ValueError, TypeError):
            app.logger.warning(f"Valor de num_servings inválido '{num_servings_from_data}' para preparación ID {preparation_id}. Manteniendo valor existente: {preparation.num_servings}")
    
    ingredients_list_to_calculate = []
    if 'ingredients' in data:
        raw_ingredients_from_frontend = data['ingredients']
        cleaned_ingredients_for_db = []
        for ing_frontend in raw_ingredients_from_frontend:
            original_description = ing_frontend.get('item', '').strip()

            quantity_payload = ing_frontend.get('quantity')
            unit_payload = ing_frontend.get('unit')

            quantity_str_from_payload = str(quantity_payload).strip() if quantity_payload is not None else ""
            unit_str_from_payload = str(unit_payload).strip() if unit_payload is not None else ""

            parsed_components = _parse_ingredient_line(f"* {original_description}")
            parsed_item_name_from_desc = parsed_components['item']
            
            is_al_gusto_item = "al gusto" in original_description.lower() or \
                               original_description.lower() in ["sal y pimienta", "sal", "pimienta", "especias"]

            if is_al_gusto_item and not quantity_str_from_payload and not unit_str_from_payload:
                quantity_val = 1.0  
                unit_to_save = "pizca" 
                app.logger.info(f"UPDATE_PREP: Item '{original_description}' detectado como 'al gusto' sin cantidad/unidad. Usando 1 pizca por defecto.")
            else:
                try:
                    quantity_val = float(quantity_str_from_payload) if quantity_str_from_payload else 0.0
                except ValueError:
                    quantity_val = 0.0
                    app.logger.warning(f"UPDATE_PREP: ValueError convirtiendo cantidad '{quantity_str_from_payload}' para '{original_description}'. Usando 0.0.")

                unit_to_save = unit_str_from_payload if unit_str_from_payload else "N/A"
                if unit_to_save == "N/A" and quantity_val == 0.0 and not is_al_gusto_item:
                     app.logger.info(f"UPDATE_PREP: Item '{original_description}' no es 'al gusto' y tiene cantidad 0 y unidad N/A.")
                elif unit_to_save == "N/A" and quantity_val != 0.0:
                     app.logger.warning(f"UPDATE_PREP: Item '{original_description}' tiene cantidad {quantity_val} pero unidad N/A. Esto podría ser un error de entrada.")

            cleaned_ingredients_for_db.append({
                'original_description': original_description,
                'parsed_item_name': parsed_item_name_from_desc,
                'quantity': quantity_val,
                'unit': unit_to_save
            })
        preparation.set_ingredients(cleaned_ingredients_for_db)
        ingredients_list_to_calculate = cleaned_ingredients_for_db
    
    # Manejar el nuevo campo 'suitability_tag' (singular) que vendrá del dropdown
    selected_tag = data.get('suitability_tag') # Esperamos un solo string del select
    if selected_tag and selected_tag.strip():
        preparation.set_suitability_tags([selected_tag]) # Guardar como lista de un solo elemento
    else:
        preparation.set_suitability_tags([]) # Si no se selecciona nada o es vacío, guardar lista vacía
    
    if not ingredients_list_to_calculate: 
        ingredients_list_to_calculate = preparation.get_ingredients()

    total_nutri = calculate_total_nutritional_info(ingredients_list_to_calculate)
    current_num_servings = preparation.num_servings if preparation.num_servings and preparation.num_servings > 0 else 1.0
    
    preparation.calories_per_serving = round(total_nutri['calories'] / current_num_servings, 2) if current_num_servings > 0 else 0.0
    preparation.protein_g_per_serving = round(total_nutri['protein_g'] / current_num_servings, 2) if current_num_servings > 0 else 0.0
    preparation.carb_g_per_serving = round(total_nutri['carb_g'] / current_num_servings, 2) if current_num_servings > 0 else 0.0
    preparation.fat_g_per_serving = round(total_nutri['fat_g'] / current_num_servings, 2) if current_num_servings > 0 else 0.0
    preparation.set_micronutrients_per_serving(total_nutri['micros'])

    preparation.updated_at = datetime.now(timezone.utc)

    try:
        db.session.commit()
        app.logger.info(f"Preparación ID {preparation_id} actualizada.")
        return jsonify(preparation.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error al actualizar preparación ID {preparation_id}: {e}", exc_info=True)
        return jsonify({'error': 'Error interno al actualizar la preparación.'}), 500


@app.route('/api/relevant_preparations_for_patient', methods=['POST'])
def get_relevant_preparations_for_patient():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No se recibieron datos del paciente.'}), 400

    initial_required_tags_log = set() # Para el log inicial
    app.logger.info(f"API relevant_preparations - Tags requeridos (inicial): {initial_required_tags_log}, Restricciones del paciente (combinadas): {data.get('aversions', []) + data.get('allergies', []) + data.get('intolerances', [])}")

    required_tags = set() # Este es el que se usará
    patient_pathologies = data.get('pathologies', [])
    
    # Obtener diet_type y other_diet_type_text DEL PAYLOAD DE ESTA API
    # El frontend debe enviar estos campos en el JSON a /api/relevant_preparations_for_patient
    patient_diet_type_from_payload = data.get('diet_type', '') 
    patient_diet_type_other_from_payload = data.get('other_diet_type_text', '')

    patient_diet_type_normalized = ""
    if patient_diet_type_other_from_payload and patient_diet_type_other_from_payload.strip():
        patient_diet_type_normalized = patient_diet_type_other_from_payload.lower().strip().replace(" ", "_")
    elif patient_diet_type_from_payload and patient_diet_type_from_payload.strip() and patient_diet_type_from_payload.lower() != 'otra':
        patient_diet_type_normalized = patient_diet_type_from_payload.lower().strip().replace(" ", "_")
    
    app.logger.debug(f"API relevant_preparations - PAYLOAD diet_type: '{patient_diet_type_from_payload}', other_diet_type_text: '{patient_diet_type_other_from_payload}', NORMALIZED for tag: '{patient_diet_type_normalized}'")

    # Mapeo explícito de diet_type normalizado a tag de UserPreparation
    # Asegúrate de que los valores de los tags coincidan con los que usas en tus UserPreparation
    # Las claves deben coincidir con patient_diet_type_normalized
    explicit_diet_to_tag_map = {
        "mediterranea": "mediterranea", # Asume que el tag es igual al tipo normalizado
        "cetogenica": "cetogenica",     # CORREGIDO: el tag almacenado es "cetogenica"
        "hiperproteica": "hiperproteica", # CORREGIDO: el tag almacenado es "hiperproteica"
        "paleo": "paleo",
        "vegetariana": "vegetariana",   # Asegúrate que el tag en tu UserPreparation sea "vegetariana" y no "vegetarina"
        "ovolactovegetariana": "ovolactovegetariana", # CORREGIDO: el tag almacenado es "ovolactovegetariana"
        "vegana": "vegana" # Asumiendo que el tag almacenado es "vegana"
        # No incluyas "otra" aquí, se maneja después si patient_diet_type_normalized no está en este mapa.
    }
    app.logger.debug(f"API relevant_preparations - explicit_diet_to_tag_map: {explicit_diet_to_tag_map}")

    if patient_diet_type_normalized:
        if patient_diet_type_normalized in explicit_diet_to_tag_map:
            tag_to_add = explicit_diet_to_tag_map[patient_diet_type_normalized]
            required_tags.add(tag_to_add)
            app.logger.debug(f"API relevant_preparations - Tag '{tag_to_add}' AÑADIDO a required_tags desde explicit_diet_to_tag_map. required_tags ahora: {required_tags}")
        elif patient_diet_type_from_payload.lower().strip() == 'otra' and patient_diet_type_other_from_payload.strip():
            # Si es un tipo "Otra" o no está en el mapa explícito, usar el valor normalizado como tag
            required_tags.add(patient_diet_type_normalized) # patient_diet_type_normalized ya es el texto de "otra" normalizado
            app.logger.debug(f"API relevant_preparations - Tag de 'Otra dieta' ('{patient_diet_type_normalized}') AÑADIDO directamente a required_tags. required_tags ahora: {required_tags}")
        elif patient_diet_type_normalized: # Para tipos de dieta no mapeados explícitamente pero que no son "Otra"
            app.logger.warning(f"API relevant_preparations - Tipo de dieta normalizado '{patient_diet_type_normalized}' no encontrado en explicit_diet_to_tag_map y no es 'Otra' con texto. No se añadió tag de dieta específico.")
    else:
        app.logger.debug("API relevant_preparations - No se especificó patient_diet_type_normalized, no se añaden tags de dieta a required_tags.")


    # Lógica para patologías y objetivos (sin cambios)
    pathology_to_tag_map = {
        "diabetes tipo i": "diabetico", "diabetes tipo ii hgo": "diabetico",
        "diabetes tipo ii insulinorequirente": "diabetico", "diabetes pregestacional": "diabetico",
        "diabetes gestacional": "diabetico_gestacional", "diabetes gestacional insulinorequirente": "diabetico_gestacional",
        "dislipidemia": "bajo_colesterol", "hipertensión arterial": "bajo_sodio",
        "insuf. renal leve-moderada": "renal_leve_mod", "insuf. renal severa": "renal_severo",
    }
    for pathology in patient_pathologies:
        tag = pathology_to_tag_map.get(pathology.lower().strip())
        if tag: required_tags.add(tag)
    if data.get('objective_low_cholesterol'): required_tags.add('bajo_colesterol')
    if data.get('objective_low_potassium'): required_tags.add('renal_bajo_potasio')

    # Procesamiento de restricciones (aversions, allergies, intolerances)
    all_patient_restrictions_lower = set()
    raw_restriction_lists = {
        "aversions": data.get('aversions', []),
        "allergies": data.get('allergies', []),
        "intolerances": data.get('intolerances', [])
    }
    for key, list_from_tagify_field in raw_restriction_lists.items():
        if isinstance(list_from_tagify_field, list):
            for item_entry in list_from_tagify_field:
                if isinstance(item_entry, str):
                    processed_value = item_entry.strip()
                    if processed_value.startswith('[') and processed_value.endswith(']'):
                        try:
                            tags_in_json = json.loads(processed_value)
                            for tag_obj in tags_in_json:
                                if isinstance(tag_obj, dict) and 'value' in tag_obj and isinstance(tag_obj['value'], str):
                                    all_patient_restrictions_lower.add(tag_obj['value'].lower().strip())
                        except json.JSONDecodeError: all_patient_restrictions_lower.add(processed_value.lower())
                    else: all_patient_restrictions_lower.add(processed_value.lower())
    
    restriction_generalizations = {
        "pescado": ["salmón", "merluza", "atún", "bacalao", "trucha", "lenguado", "sardina", "jurel", "caballa", "tilapia", "corvina"],
        "marisco": ["gamba", "langostino", "camarón", "mejillón", "almeja", "vieira", "calamar", "pulpo", "cangrejo", "langosta"],
        "frutos secos": ["nuez", "almendra", "avellana", "pistacho", "anacardo", "marañón", "pecana", "maní", "cacahuete"],
        "gluten": ["trigo", "avena", "cebada", "centeno", "espelta", "kamut", "triticale", "harina de trigo", "pan", "pasta", "galletas", "sémola"],
        "lactosa": ["leche", "queso", "yogur", "nata", "crema", "mantequilla", "suero de leche", "caseína", "lactosa"], # Duplicado con lacteos, revisar
        "lacteos": ["leche", "queso", "yogur", "nata", "crema", "mantequilla", "suero de leche", "caseína", "lactosa"],
        "huevo": ["huevo", "clara de huevo", "yema de huevo", "ovoalbúmina", "albumina"],
        "soja": ["soja", "tofu", "tempeh", "edamame", "salsa de soja", "miso", "proteína de soja", "lecitina de soja"],
    }

    app.logger.info(f"API relevant_preparations - Tags requeridos FINAL: {required_tags}, Restricciones del paciente (combinadas): {all_patient_restrictions_lower}")

    all_preparations = UserPreparation.query.all()
    relevant_preparations = []
    skipped_due_to_restriction_count = 0

    for prep in all_preparations:
        prep_tags = set(prep.get_suitability_tags())
        
        if not prep_tags:
            app.logger.debug(f"Prep ID {prep.id} ({prep.name}) SKIPPED (NO TAGS): La preparación no tiene tags de adecuación.")
            continue
            
        if required_tags and not required_tags.issubset(prep_tags):
            app.logger.debug(f"Prep ID {prep.id} ({prep.name}) SKIPPED (TAGS): No cumple todos los required_tags. Prep tags: {prep_tags}, Required: {required_tags}")
            continue

        if all_patient_restrictions_lower:
            skip_prep_due_to_restriction = False
            prep_ingredient_items_lower = {ing.get('parsed_item_name', '').lower().strip() for ing in prep.get_ingredients() if ing.get('parsed_item_name')}
            prep_name_lower = prep.name.lower()
            app.logger.debug(f"Prep ID {prep.id} ({prep.name}) - Checking restrictions. Prep ingredients (parsed): {prep_ingredient_items_lower}. Prep name: {prep_name_lower}. Patient restrictions: {all_patient_restrictions_lower}")
            for restriction_item in all_patient_restrictions_lower:
                search_term = restriction_item
                if any(search_term in prep_ingredient_item for prep_ingredient_item in prep_ingredient_items_lower) or \
                   search_term in prep_name_lower:
                    location_found = "ingredient" if any(search_term in prep_ingredient_item for prep_ingredient_item in prep_ingredient_items_lower) else "name"
                    app.logger.debug(f"Prep ID {prep.id} ('{prep.name}') SKIPPED due to SUBSTRING restriction: '{search_term}' found in prep {location_found}.")
                    skip_prep_due_to_restriction = True; break
                if search_term in restriction_generalizations:
                    specific_items_to_avoid = restriction_generalizations[search_term]
                    for specific_avoid_item in specific_items_to_avoid:
                        if any(specific_avoid_item in prep_ingredient_item for prep_ingredient_item in prep_ingredient_items_lower) or \
                           specific_avoid_item in prep_name_lower:
                            gen_location_found = "ingredient" if any(specific_avoid_item in prep_ingredient_item for prep_ingredient_item in prep_ingredient_items_lower) else "name"
                            app.logger.debug(f"Prep ID {prep.id} ('{prep.name}') SKIPPED due to GENERALIZED restriction: category '{search_term}' matched specific item '{specific_avoid_item}' in prep {gen_location_found}.")
                            skip_prep_due_to_restriction = True; break
                    if skip_prep_due_to_restriction: break
            if skip_prep_due_to_restriction:
                skipped_due_to_restriction_count += 1
                continue
        
        app.logger.debug(f"Prep ID {prep.id} ('{prep.name}') ADDED. Tags: {prep_tags}, Ingredients (parsed_item_name): {[ing.get('parsed_item_name') for ing in prep.get_ingredients()]}")
        relevant_preparations.append(prep.to_dict())
    
    app.logger.info(f"Encontradas {len(relevant_preparations)} preparaciones relevantes. Omitidas por restricción: {skipped_due_to_restriction_count}.")
    return jsonify(relevant_preparations), 200

# --- API para Perfil de Usuario ---

@app.route('/profile')
@login_required
def profile_page():
    return render_template('profile.html', countries=app.config['COUNTRIES'], professions=app.config['PROFESSIONS'])

@app.route('/api/user_info', methods=['GET'])
@firebase_auth_required
def get_user_info():
    user = g.user
    app.logger.info(f"API /api/user_info: Devolviendo info para usuario ID: {user.id} ({user.email})")
    return jsonify(user.to_dict())

@app.route('/api/user/profile', methods=['PUT'])
@firebase_auth_required
def update_user_profile():
    user = g.user
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No se recibieron datos.'}), 400

    try:
        user.name = data.get('name', user.name)
        user.surname = data.get('surname', user.surname)
        user.profession = data.get('profession', user.profession)
        user.license_number = data.get('license_number', user.license_number)
        user.city = data.get('city', user.city)
        user.country = data.get('country', user.country)
        user.address = data.get('address', user.address)
        user.phone_number = data.get('phone_number', user.phone_number)
        
        db.session.commit()
        app.logger.info(f"Perfil del usuario ID {user.id} actualizado.")
        return jsonify({'message': 'Perfil actualizado correctamente.'}), 200
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error al actualizar perfil del usuario ID {user.id}: {e}", exc_info=True)
        return jsonify({'error': 'Error interno al actualizar el perfil.'}), 500

# --- API para Ingredientes ---

@app.route('/mis_ingredientes')
@login_required
def mis_ingredientes_view():
    return render_template('mis_ingredientes.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Has cerrado sesión exitosamente.', 'success')
    return redirect(url_for('login_page'))

@app.route('/api/ingredients', methods=['GET'])
@firebase_auth_required
def get_ingredients():
    ingredients = Ingredient.query.order_by(Ingredient.name).all()
    app.logger.info(f"API GET /api/ingredients: Devolviendo {len(ingredients)} ingredientes.")
    return jsonify([ing.to_dict() for ing in ingredients])

@app.route('/api/ingredients/<int:ingredient_id>', methods=['GET'])
@firebase_auth_required
def get_ingredient(ingredient_id):
    try:
        ingredient = Ingredient.query.get_or_404(ingredient_id)
        return jsonify(ingredient.to_dict()), 200
    except Exception as e:
        app.logger.error(f"API GET /api/ingredients/{ingredient_id}: Error al obtener ingrediente: {e}", exc_info=True)
        return jsonify({'error': 'Error interno al obtener el ingrediente.'}), 500

@app.route('/api/ingredients', methods=['POST'])
@firebase_auth_required
def create_ingredient():
    data = request.get_json()
    name = data.get('name', '').strip()
    synonyms = data.get('synonyms', [])

    try:
        if not name:
            raise ValueError("El nombre del ingrediente es requerido.")
        if Ingredient.query.filter(Ingredient.name.ilike(name)).first():
            raise ValueError("Ya existe un ingrediente con ese nombre.")

        calories = validate_numeric_field(data.get('calories'), "Calorías", min_val=0)
        protein_g = validate_numeric_field(data.get('protein_g'), "Proteínas", min_val=0)
        carb_g = validate_numeric_field(data.get('carb_g'), "Carbohidratos", min_val=0)
        fat_g = validate_numeric_field(data.get('fat_g'), "Grasas", min_val=0)

    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400

    new_ingredient = Ingredient(name=name)
    new_ingredient.set_synonyms(synonyms)

    if any(v is not None for v in [calories, protein_g, carb_g, fat_g]):
        nutrient_entry = IngredientNutrient(
            ingredient=new_ingredient, reference_quantity=100.0, reference_unit='g',
            calories=calories, protein_g=protein_g, carb_g=carb_g, fat_g=fat_g
        )
        db.session.add(nutrient_entry)

    try:
        db.session.add(new_ingredient)
        db.session.commit()
        return jsonify(new_ingredient.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': 'Error interno al crear el ingrediente.'}), 500

@app.route('/api/ingredients/<int:ingredient_id>', methods=['PUT'])
@firebase_auth_required
def update_ingredient(ingredient_id):
    ingredient = Ingredient.query.get_or_404(ingredient_id)
    data = request.get_json()
    new_name = data.get('name', '').strip()
    if new_name and new_name.lower() != ingredient.name.lower() and Ingredient.query.filter(Ingredient.name.ilike(new_name)).first():
        return jsonify({'error': 'Ya existe otro ingrediente con ese nombre.'}), 409

    ingredient.name = new_name if new_name else ingredient.name
    ingredient.set_synonyms(data.get('synonyms', []))

    try:
        calories = validate_numeric_field(data.get('calories'), "Calorías", min_val=0)
        protein_g = validate_numeric_field(data.get('protein_g'), "Proteínas", min_val=0)
        carb_g = validate_numeric_field(data.get('carb_g'), "Carbohidratos", min_val=0)
        fat_g = validate_numeric_field(data.get('fat_g'), "Grasas", min_val=0)
    except ValueError as ve:
        return jsonify({'error': str(ve)}), 400

    nutrient_entry = ingredient.nutrients.filter_by(reference_quantity=100.0, reference_unit='g').first()
    if not nutrient_entry and any(v is not None for v in [calories, protein_g, carb_g, fat_g]):
        nutrient_entry = IngredientNutrient(ingredient=ingredient, reference_quantity=100.0, reference_unit='g')
        db.session.add(nutrient_entry)

    if nutrient_entry:
        if calories is not None:
            nutrient_entry.calories = calories
        if protein_g is not None:
            nutrient_entry.protein_g = protein_g
        if carb_g is not None:
            nutrient_entry.carb_g = carb_g
        if fat_g is not None:
            nutrient_entry.fat_g = fat_g

    db.session.commit()
    return jsonify(ingredient.to_dict()), 200

@app.route('/api/ingredients/<int:ingredient_id>', methods=['DELETE'])
@firebase_auth_required
def delete_ingredient(ingredient_id):
    ingredient = Ingredient.query.get_or_404(ingredient_id)
    db.session.delete(ingredient)
    db.session.commit()
    return jsonify({'message': 'Ingrediente eliminado correctamente.'}), 200


# --- Bloque de Arranque del Servidor ---
if __name__ == '__main__':
    app.run(debug=True)
