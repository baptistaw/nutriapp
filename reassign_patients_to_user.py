import os
from app import app, db, User, Patient, Evaluation, UserPreparation
from sqlalchemy import or_ # Importar or_ para condiciones OR

def reassign_data_to_real_user():
    with app.app_context():
        print("Iniciando reasignación de datos de pacientes y evaluaciones...")

        # 1. Encontrar el usuario placeholder
        placeholder_user = User.query.filter_by(firebase_uid='DEFAULT_USER_PLACEHOLDER_UID').first()
        if not placeholder_user:
            print("Error: No se encontró el usuario placeholder 'DEFAULT_USER_PLACEHOLDER_UID'.")
            print("Asegúrate de que la migración se ejecutó correctamente y creó este usuario.")
            return
        print(f"Usuario placeholder encontrado: ID {placeholder_user.id}, Email: {placeholder_user.email}")

        # 2. Encontrar el usuario real (el primero que no sea el placeholder)
        # Esto asume que ya has iniciado sesión al menos una vez con tu cuenta real de Firebase.
        real_user = User.query.filter(User.firebase_uid != 'DEFAULT_USER_PLACEHOLDER_UID').first()
        if not real_user:
            print("Error: No se encontró ningún usuario real en la base de datos.")
            print("Por favor, inicia sesión en la aplicación al menos una vez con tu cuenta de Firebase real y luego ejecuta este script de nuevo.")
            return
        print(f"Usuario real encontrado: ID {real_user.id}, Email: {real_user.email}, UID: {real_user.firebase_uid}")

        if placeholder_user.id == real_user.id:
            print("Advertencia: El usuario placeholder y el usuario real son el mismo. No se necesita reasignación.")
            return

        # 3. Reasignar pacientes: Actualizar pacientes que pertenecen al usuario placeholder O tienen user_id NULL
        patients_updated_count = Patient.query.filter(
            or_(Patient.user_id == placeholder_user.id, Patient.user_id.is_(None))
        ).update({"user_id": real_user.id}, synchronize_session=False)
        print(f"Reasignados {patients_updated_count} pacientes del usuario placeholder/NULL al usuario real.")

        # 4. Reasignar evaluaciones: Actualizar evaluaciones que pertenecen al usuario placeholder O tienen user_id NULL
        evaluations_updated_count = Evaluation.query.filter(
            or_(Evaluation.user_id == placeholder_user.id, Evaluation.user_id.is_(None))
        ).update({"user_id": real_user.id}, synchronize_session=False)
        print(f"Reasignadas {evaluations_updated_count} evaluaciones del usuario placeholder/NULL al usuario real.")

        # 5. Reasignar preparaciones de usuario: Actualizar preparaciones que pertenecen al usuario placeholder O tienen user_id NULL
        preparations_updated_count = UserPreparation.query.filter(
            or_(UserPreparation.user_id == placeholder_user.id, UserPreparation.user_id.is_(None))
        ).update({"user_id": real_user.id}, synchronize_session=False)
        print(f"Reasignadas {preparations_updated_count} preparaciones de usuario del usuario placeholder/NULL al usuario real.")

        # 6. Eliminar el usuario placeholder
        db.session.delete(placeholder_user)
        print(f"Usuario placeholder (ID: {placeholder_user.id}) marcado para eliminación.")

        # 7. Confirmar los cambios
        try:
            db.session.commit()
            print("\n¡Reasignación completada exitosamente! Tus pacientes y datos anteriores deberían ser visibles ahora.")
        except Exception as e:
            db.session.rollback()
            print(f"Error durante el commit final de la reasignación: {e}")
            print("Los cambios han sido revertidos. Por favor, revisa el error.")

if __name__ == '__main__':
    reassign_data_to_real_user()
