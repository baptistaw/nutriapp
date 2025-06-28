# migrate_preparation_ingredients.py
import json
from app import app, db, UserPreparation, _parse_ingredient_line # Asegúrate de que _parse_ingredient_line sea importable

def migrate_ingredients_format():
    with app.app_context():
        preparations_to_migrate = UserPreparation.query.all()
        migrated_count = 0
        skipped_count = 0
        error_count = 0

        print(f"Iniciando migración para {len(preparations_to_migrate)} preparaciones...")

        for prep in preparations_to_migrate:
            try:
                old_ingredients_json = prep.ingredients_json
                if not old_ingredients_json:
                    print(f"Preparación ID {prep.id} ('{prep.name}') no tiene ingredients_json. Saltando.")
                    skipped_count += 1
                    continue

                old_ingredients_list = json.loads(old_ingredients_json)

                # Verificar si ya está en el nuevo formato (simple check)
                if old_ingredients_list and isinstance(old_ingredients_list[0], dict) and \
                   'parsed_item_name' in old_ingredients_list[0] and \
                   'original_description' in old_ingredients_list[0]:
                    print(f"Preparación ID {prep.id} ('{prep.name}') ya parece estar en el nuevo formato. Saltando.")
                    skipped_count += 1
                    continue
                
                new_ingredients_list = []
                for old_ing_data in old_ingredients_list:
                    if not isinstance(old_ing_data, dict) or 'item' not in old_ing_data:
                        print(f"  WARN: Formato de ingrediente inesperado en Preparación ID {prep.id}: {old_ing_data}. Saltando este ingrediente.")
                        new_ingredients_list.append(old_ing_data) # Mantener el dato problemático tal cual o decidir omitirlo
                        continue

                    original_description = old_ing_data.get('item', '')
                    quantity_from_old = old_ing_data.get('quantity', '0')
                    unit_from_old = old_ing_data.get('unit', 'N/A')

                    # Usar _parse_ingredient_line para obtener el nombre base limpio
                    # La cantidad y unidad de _parse_ingredient_line se ignoran aquí,
                    # ya que usamos las que ya estaban guardadas.
                    parsed_components = _parse_ingredient_line(f"* {original_description}")
                    parsed_item_name_from_desc = parsed_components['item']
                    
                    new_ingredients_list.append({
                        'original_description': original_description,
                        'parsed_item_name': parsed_item_name_from_desc,
                        'quantity': quantity_from_old,
                        'unit': unit_from_old
                    })
                
                prep.ingredients_json = json.dumps(new_ingredients_list)
                migrated_count += 1
                print(f"Preparación ID {prep.id} ('{prep.name}') migrada.")

            except json.JSONDecodeError as e:
                print(f"Error decodificando JSON para Preparación ID {prep.id}: {e}. ingredients_json: '{prep.ingredients_json}'. Saltando.")
                error_count += 1
            except Exception as e:
                print(f"Error inesperado migrando Preparación ID {prep.id}: {e}")
                error_count += 1
                db.session.rollback() # Revertir cambios para esta preparación si algo falla
                # Considera si quieres continuar con otras o detener el script

        if error_count == 0:
            try:
                db.session.commit()
                print("\n--- Migración Completada ---")
                print(f"Preparaciones migradas exitosamente: {migrated_count}")
                print(f"Preparaciones omitidas (sin datos o ya migradas): {skipped_count}")
            except Exception as e:
                db.session.rollback()
                print(f"Error durante el commit final de la migración: {e}")
                print("TODOS LOS CAMBIOS DE LA MIGRACIÓN HAN SIDO REVERTIDOS.")
        else:
            db.session.rollback() # Asegurar rollback si hubo errores individuales
            print("\n--- Migración Finalizada con Errores ---")
            print(f"Preparaciones migradas (antes del error final o rollback): {migrated_count}")
            print(f"Preparaciones omitidas: {skipped_count}")
            print(f"Preparaciones con errores (no migradas o revertidas): {error_count}")
            print("SE RECOMIENDA REVISAR LOS ERRORES. NINGÚN CAMBIO HA SIDO GUARDADO PERMANENTEMENTE SI HUBO ERRORES.")


if __name__ == '__main__':
    confirm = input("Este script modificará los datos de 'ingredients_json' en la tabla 'user_preparation'.\n"
                    "Se recomienda hacer una copia de seguridad de la base de datos antes de continuar.\n"
                    "¿Desea continuar? (s/N): ")
    if confirm.lower() == 's':
        print("Iniciando script de migración...")
        migrate_ingredients_format()
        print("Script de migración finalizado.")
    else:
        print("Migración cancelada por el usuario.")

