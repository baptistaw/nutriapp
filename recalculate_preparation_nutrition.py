# recalculate_preparation_nutrition.py
import json
from app import app, db, UserPreparation, calculate_total_nutritional_info, _parse_ingredient_line

def recalculate_all_preparations_nutrition():
    with app.app_context():
        preparations = UserPreparation.query.all()
        updated_count = 0
        skipped_count = 0
        error_count = 0

        print(f"Iniciando recálculo nutricional para {len(preparations)} preparaciones...")

        for prep in preparations:
            try:
                # Solo recalcular si no tiene calorías o si quieres forzarlo
                if prep.calories_per_serving is not None and prep.calories_per_serving > 0:
                     # print(f"Preparación ID {prep.id} ('{prep.name}') ya tiene info nutricional. Saltando recálculo.")
                     # skipped_count +=1
                     # continue # Descomenta para saltar si ya tiene datos
                     pass # Comentado para forzar recálculo y asegurar consistencia

                ingredients_from_db = prep.get_ingredients() # Esto ya debería tener la nueva estructura
                
                if not ingredients_from_db:
                    print(f"Preparación ID {prep.id} ('{prep.name}') no tiene ingredientes. Saltando.")
                    skipped_count += 1
                    continue

                # 'ingredients_from_db' ya tiene 'parsed_item_name', 'quantity', 'unit'
                # No es necesario re-parsear con _parse_ingredient_line aquí si la migración fue correcta
                # y si los nuevos guardados usan la estructura correcta.
                
                print(f"Recalculando para Prep ID {prep.id} ('{prep.name}'). Ingredientes: {ingredients_from_db}")
                
                total_nutri = calculate_total_nutritional_info(ingredients_from_db)
                
                num_servings = prep.num_servings if prep.num_servings and prep.num_servings > 0 else 1.0

                prep.calories_per_serving = round(total_nutri['calories'] / num_servings, 2)
                prep.protein_g_per_serving = round(total_nutri['protein_g'] / num_servings, 2)
                prep.carb_g_per_serving = round(total_nutri['carb_g'] / num_servings, 2)
                prep.fat_g_per_serving = round(total_nutri['fat_g'] / num_servings, 2)
                prep.set_micronutrients_per_serving(total_nutri['micros'])
                
                prep.updated_at = db.func.now() # Actualizar timestamp
                updated_count += 1
                print(f"  Info nutricional recalculada para Prep ID {prep.id}: Cal={prep.calories_per_serving}")

            except Exception as e:
                print(f"Error recalculando para Preparación ID {prep.id} ('{prep.name}'): {e}")
                error_count += 1
                # No hacer rollback aquí para no perder otros recálculos,
                # pero podrías querer loguear más detalles o manejarlo diferente.

        if error_count == 0:
            try:
                db.session.commit()
                print("\n--- Recálculo Nutricional Completado ---")
                print(f"Preparaciones actualizadas/recalculadas: {updated_count}")
                print(f"Preparaciones omitidas: {skipped_count}")
            except Exception as e:
                db.session.rollback()
                print(f"Error durante el commit final del recálculo: {e}")
        else:
            # Considera si quieres hacer un rollback total si hubo errores individuales
            # db.session.rollback() 
            print("\n--- Recálculo Nutricional Finalizado con Errores ---")
            print(f"Preparaciones actualizadas (antes de error/rollback): {updated_count}")
            print(f"Preparaciones omitidas: {skipped_count}")
            print(f"Preparaciones con errores durante el recálculo: {error_count}")

if __name__ == '__main__':
    confirm = input("Este script intentará recalcular la información nutricional para TODAS las UserPreparations.\n"
                    "Se recomienda hacer una copia de seguridad de la base de datos antes de continuar.\n"
                    "¿Desea continuar? (s/N): ")
    if confirm.lower() == 's':
        print("Iniciando script de recálculo nutricional...")
        recalculate_all_preparations_nutrition()
        print("Script de recálculo nutricional finalizado.")
    else:
        print("Recálculo cancelado por el usuario.")

