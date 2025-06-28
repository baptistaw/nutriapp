
# ---------------------------------------------------------------------------
# populate_equivalences.py
import json
from app import app, db, Ingredient, UnitEquivalence

equivalences_data = [
    # CEREALES Y DERIVADOS
    {"ingredient_name": "Arroz, grano, blanco, pulido, hervido",           "household_unit": "taza",               "grams_per_unit": 158.0},
    {"ingredient_name": "Arroz, grano, blanco, pulido, hervido",           "household_unit": "cucharada sopera",   "grams_per_unit": 10.0},
    {"ingredient_name": "Avena, grano, arrollada, hervida",                "household_unit": "taza",               "grams_per_unit": 234.0},
    {"ingredient_name": "Avena, grano, arrollada, hervida",                "household_unit": "cucharada sopera",   "grams_per_unit": 15.0},
    {"ingredient_name": "Cebada, grano, mondado, perlado, hervido",        "household_unit": "taza",               "grams_per_unit": 157.0},
    {"ingredient_name": "Cebada, grano, mondado, perlado, hervido",        "household_unit": "cucharada sopera",   "grams_per_unit": 10.0},
    {"ingredient_name": "Maiz, choclo",                                    "household_unit": "unidad mediana",     "grams_per_unit": 90.0},
    {"ingredient_name": "Maiz, choclo",                                    "household_unit": "taza",               "grams_per_unit": 145.0},
    {"ingredient_name": "Maiz, grano, entero (Zea mays spp)",              "household_unit": "taza",               "grams_per_unit": 160.0},
    {"ingredient_name": "Maiz, grano, entero (Zea mays spp)",              "household_unit": "cucharada sopera",   "grams_per_unit": 10.0},
    {"ingredient_name": "Maiz, harina amarilla (polenta), cruda",          "household_unit": "taza",               "grams_per_unit": 150.0},
    {"ingredient_name": "Maiz, harina amarilla (polenta), cruda",          "household_unit": "cucharada sopera",   "grams_per_unit": 9.0},
    {"ingredient_name": "Maiz, harina amarilla (polenta), hervida",        "household_unit": "taza",               "grams_per_unit": 250.0},
    {"ingredient_name": "Maiz, harina amarilla (polenta), hervida",        "household_unit": "cucharada sopera",   "grams_per_unit": 16.0},
    {"ingredient_name": "Trigo, pan francés",                              "household_unit": "unidad mediana",     "grams_per_unit": 50.0},
    {"ingredient_name": "Trigo, pan francés",                              "household_unit": "rebanada mediana",   "grams_per_unit": 25.0},
    {"ingredient_name": "Centeno, pan",                                    "household_unit": "rebanada mediana",   "grams_per_unit": 30.0},
    {"ingredient_name": "Centeno, pan",                                    "household_unit": "taza",               "grams_per_unit": 60.0},
    {"ingredient_name": "Trigo, pan de salvado",                           "household_unit": "rebanada mediana",   "grams_per_unit": 28.0},
    {"ingredient_name": "Trigo, pan de salvado",                           "household_unit": "taza",               "grams_per_unit": 56.0},
    {"ingredient_name": "Centeno, harina, clara, cruda",                   "household_unit": "taza",               "grams_per_unit": 112.0},
    {"ingredient_name": "Centeno, harina, clara, cruda",                   "household_unit": "cucharada sopera",   "grams_per_unit": 7.0},
    {"ingredient_name": "Cebada, harina \"Genser\", cruda",                "household_unit": "taza",               "grams_per_unit": 120.0},
    {"ingredient_name": "Cebada, harina \"Genser\", cruda",                "household_unit": "cucharada sopera",   "grams_per_unit": 7.5},

    # LEGUMBRES, SEMILLAS Y FRUTOS SECOS
    {"ingredient_name": "Lenteja, semilla entera, seca, cruda",            "household_unit": "taza",               "grams_per_unit": 190.0},
    {"ingredient_name": "Lenteja, semilla entera, seca, cruda",            "household_unit": "cucharada sopera",   "grams_per_unit": 12.0},
    {"ingredient_name": "Garbanzo, grano entero, seco, hervido",           "household_unit": "taza",               "grams_per_unit": 164.0},
    {"ingredient_name": "Garbanzo, grano entero, seco, hervido",           "household_unit": "cucharada sopera",   "grams_per_unit": 10.0},
    {"ingredient_name": "Poroto Negro, semilla, seco",                     "household_unit": "taza",               "grams_per_unit": 185.0},
    {"ingredient_name": "Poroto Negro, semilla, seco",                     "household_unit": "cucharada sopera",   "grams_per_unit": 12.0},
    {"ingredient_name": "Soja, semilla madura, seca, cruda",               "household_unit": "taza",               "grams_per_unit": 186.0},
    {"ingredient_name": "Soja, semilla madura, seca, cruda",               "household_unit": "cucharada sopera",   "grams_per_unit": 12.0},
    {"ingredient_name": "Maní, semilla con piel, crudo",                   "household_unit": "taza",               "grams_per_unit": 146.0},
    {"ingredient_name": "Maní, semilla con piel, crudo",                   "household_unit": "cucharada sopera",   "grams_per_unit": 9.0},
    {"ingredient_name": "Maní, semilla sin piel, tostado",                 "household_unit": "taza",               "grams_per_unit": 140.0},
    {"ingredient_name": "Maní, semilla sin piel, tostado",                 "household_unit": "cucharada sopera",   "grams_per_unit": 8.5},
    {"ingredient_name": "Nuez, pepita",                                    "household_unit": "taza",               "grams_per_unit": 93.0},
    {"ingredient_name": "Nuez, pepita",                                    "household_unit": "cucharada sopera",   "grams_per_unit": 6.0},
    {"ingredient_name": "Avellana, pepita, seca",                          "household_unit": "taza",               "grams_per_unit": 135.0},
    {"ingredient_name": "Avellana, pepita, seca",                          "household_unit": "cucharada sopera",   "grams_per_unit": 8.0},
    {"ingredient_name": "Semilla de girasol, sin cáscara",                 "household_unit": "taza",               "grams_per_unit": 140.0},
    {"ingredient_name": "Semilla de girasol, sin cáscara",                 "household_unit": "cucharada sopera",   "grams_per_unit": 9.0},
    {"ingredient_name": "Semilla de chía, seca",                           "household_unit": "taza",               "grams_per_unit": 170.0},
    {"ingredient_name": "Semilla de chía, seca",                           "household_unit": "cucharada sopera",   "grams_per_unit": 12.0},

    # VERDURAS Y HORTALIZAS
    {"ingredient_name": "Cebolla blanca, bulbo, cruda",                    "household_unit": "unidad mediana",     "grams_per_unit": 110.0},
    {"ingredient_name": "Cebolla blanca, bulbo, cruda",                    "household_unit": "taza",               "grams_per_unit": 160.0},
    {"ingredient_name": "Tomate, fresco, crudo",                           "household_unit": "unidad mediana",     "grams_per_unit": 123.0},
    {"ingredient_name": "Tomate, fresco, crudo",                           "household_unit": "taza",               "grams_per_unit": 180.0},
    {"ingredient_name": "Zanahoria, raiz, pelada, fresca",                 "household_unit": "unidad mediana",     "grams_per_unit": 61.0},
    {"ingredient_name": "Zanahoria, raiz, pelada, fresca",                 "household_unit": "taza",               "grams_per_unit": 110.0},
    {"ingredient_name": "Espinaca, hoja, fresca, cruda",                   "household_unit": "taza",               "grams_per_unit": 30.0},
    {"ingredient_name": "Espinaca, hoja, fresca, cruda",                   "household_unit": "cucharada sopera",   "grams_per_unit": 5.0},
    {"ingredient_name": "Papa, tubérculo, fresca, cruda",                  "household_unit": "taza",               "grams_per_unit": 150.0},
    {"ingredient_name": "Zapallito, parte tierna, fresco, crudo",          "household_unit": "unidad mediana",     "grams_per_unit": 180.0},
    {"ingredient_name": "Zapallito, parte tierna, fresco, crudo",          "household_unit": "taza",               "grams_per_unit": 115.0},
    {"ingredient_name": "Lechuga, hoja, fresca",                           "household_unit": "taza",               "grams_per_unit": 28.0},
    {"ingredient_name": "Lechuga, hoja, fresca",                           "household_unit": "cucharada sopera",   "grams_per_unit": 4.0},
    {"ingredient_name": "Morrón rojo, fruto, fresco, crudo",               "household_unit": "unidad mediana",     "grams_per_unit": 119.0},
    {"ingredient_name": "Morrón rojo, fruto, fresco, crudo",               "household_unit": "taza",               "grams_per_unit": 150.0},
    {"ingredient_name": "Brocoli, flor, fresco, crudo",                    "household_unit": "taza",               "grams_per_unit": 91.0},
    {"ingredient_name": "Brocoli, flor, fresco, crudo",                    "household_unit": "unidad mediana",     "grams_per_unit": 608.0},
    {"ingredient_name": "Calabaza, pulpa, fresca, cruda",                  "household_unit": "taza",               "grams_per_unit": 116.0},
    {"ingredient_name": "Calabaza, pulpa, fresca, cruda",                  "household_unit": "unidad mediana",     "grams_per_unit": 1000.0},

    # FRUTAS
    {"ingredient_name": "Banana, pulpa, fresca",                           "household_unit": "taza",               "grams_per_unit": 150.0},
    {"ingredient_name": "Manzana, pulpa, fresca, cruda",                   "household_unit": "taza",               "grams_per_unit": 125.0},
    {"ingredient_name": "Pera, pulpa, fresca, cruda",                      "household_unit": "unidad mediana",     "grams_per_unit": 178.0},
    {"ingredient_name": "Pera, pulpa, fresca, cruda",                      "household_unit": "taza",               "grams_per_unit": 140.0},
    {"ingredient_name": "Ananá, pulpa, fresco",                            "household_unit": "taza",               "grams_per_unit": 165.0},
    {"ingredient_name": "Ananá, pulpa, fresco",                            "household_unit": "rebanada mediana",   "grams_per_unit": 84.0},
    {"ingredient_name": "Frutilla pulpa, fresca, cruda",                   "household_unit": "taza",               "grams_per_unit": 152.0},
    {"ingredient_name": "Frutilla pulpa, fresca, cruda",                   "household_unit": "cucharada sopera",   "grams_per_unit": 9.0},
    {"ingredient_name": "Durazno, pulpa, fresca, cruda",                   "household_unit": "unidad mediana",     "grams_per_unit": 150.0},
    {"ingredient_name": "Durazno, pulpa, fresca, cruda",                   "household_unit": "taza",               "grams_per_unit": 154.0},
    {"ingredient_name": "Uva, pulpa, fresca, cruda",                       "household_unit": "taza",               "grams_per_unit": 151.0},

    # LÁCTEOS
    {"ingredient_name": "Yogur entero natural",                            "household_unit": "taza",               "grams_per_unit": 245.0},
    {"ingredient_name": "Yogur descremado",                                "household_unit": "taza",               "grams_per_unit": 245.0},
    {"ingredient_name": "Yogur entero saborizado",                         "household_unit": "taza",               "grams_per_unit": 245.0},
    {"ingredient_name": "Leche de vaca parcialm. descremada, adic. con vit A y D", "household_unit": "taza",    "grams_per_unit": 244.0},
    {"ingredient_name": "Queso mozzarella",                                "household_unit": "rebanada mediana",   "grams_per_unit": 28.0},
    {"ingredient_name": "Queso mozzarella",                                "household_unit": "taza",               "grams_per_unit": 113.0},
    {"ingredient_name": "Queso cuartirolo",                                "household_unit": "rebanada mediana",   "grams_per_unit": 28.0},
    {"ingredient_name": "Queso cuartirolo",                                "household_unit": "taza",               "grams_per_unit": 110.0},
    {"ingredient_name": "Queso crema, entero, untable ",                   "household_unit": "cucharada sopera",   "grams_per_unit": 14.0},
    {"ingredient_name": "Queso crema, entero, untable ",                   "household_unit": "taza",               "grams_per_unit": 225.0},

    # CARNES Y PESCADOS
    {"ingredient_name": "Pollo, asado al horno",                           "household_unit": "porción mediana",    "grams_per_unit": 120.0},
    {"ingredient_name": "Vacuno, lomo, fresco, crudo",                     "household_unit": "porción mediana",    "grams_per_unit": 100.0},
    {"ingredient_name": "Cerdo, panceta",                                  "household_unit": "rebanada mediana",   "grams_per_unit": 20.0},
    {"ingredient_name": "Merluza, fresca, cruda, carne",                   "household_unit": "porción mediana",    "grams_per_unit": 100.0},

    # GRASAS Y ENDULZANTES
    {"ingredient_name": "Manteca, fresca",                                 "household_unit": "cucharada sopera",   "grams_per_unit": 14.0},
    {"ingredient_name": "Manteca, fresca",                                 "household_unit": "taza",               "grams_per_unit": 227.0},
    {"ingredient_name": "Margarina 100% vegetal Manty (en pote y en pan)", "household_unit": "cucharada sopera",   "grams_per_unit": 14.0},
    {"ingredient_name": "Margarina 100% vegetal Manty (en pote y en pan)", "household_unit": "taza",               "grams_per_unit": 227.0},
    {"ingredient_name": "Miel, pura",                                      "household_unit": "cucharada sopera",   "grams_per_unit": 21.0},
    {"ingredient_name": "Miel, pura",                                      "household_unit": "taza",               "grams_per_unit": 340.0},
    {"ingredient_name": "Azúcar, impalpable",                              "household_unit": "taza",               "grams_per_unit": 120.0},
    {"ingredient_name": "Azúcar, impalpable",                              "household_unit": "cucharada sopera",   "grams_per_unit": 7.5},

    # CONDIMENTOS Y BEBIDAS
    {"ingredient_name": "Sal, de mesa, yodada",                            "household_unit": "cucharadita de té",  "grams_per_unit": 6.0},
    {"ingredient_name": "Sal, de mesa, yodada",                            "household_unit": "taza",               "grams_per_unit": 292.0},
    {"ingredient_name": "Jugo de naranja, fresco",                         "household_unit": "taza",               "grams_per_unit": 248.0},
    {"ingredient_name": "Jugo de limón, fresco",                           "household_unit": "cucharada sopera",   "grams_per_unit": 15.0},
    {"ingredient_name": "Jugo de limón, fresco",                           "household_unit": "unidad mediana",     "grams_per_unit": 65.0},
    {"ingredient_name": "Vinagre de vino tinto",                           "household_unit": "cucharadita de té",  "grams_per_unit": 5.0}, # Asumiendo densidad ~1g/ml
    # En populate_equivalences.py, dentro de equivalences_data = [ ... ]
    {"ingredient_name": "Aceite de oliva Cocinero", "household_unit": "ml", "grams_per_unit": 0.92}, 
    # Opcional, pero bueno tenerla si la IA la sugiere:
    {"ingredient_name": "Aceite de oliva Cocinero", "household_unit": "cucharada sopera", "grams_per_unit": 13.8}, # Asumiendo 1 cda = 15ml; 15ml * 0.92g/ml = 13.8g
]
# ---------------------------------------------------------------------------


def populate_equivalences():
    with app.app_context():
        count_added = 0
        count_skipped_ingredient_not_found = 0
        count_skipped_already_exists = 0

        for eq_data in equivalences_data:
            ingredient_name = eq_data.get("ingredient_name")
            household_unit = eq_data.get("household_unit", "").lower().strip() # Normalizar
            grams_per_unit = eq_data.get("grams_per_unit")

            if not ingredient_name or not household_unit or grams_per_unit is None:
                print(f"Datos incompletos para la equivalencia: {eq_data}. Saltando.")
                continue

            # Buscar el ingrediente en la base de datos
            ingredient = Ingredient.query.filter_by(name=ingredient_name).first()

            if not ingredient:
                print(f"Ingrediente '{ingredient_name}' no encontrado en la base de datos. Saltando equivalencia para '{household_unit}'.")
                count_skipped_ingredient_not_found += 1
                continue

            # Verificar si la equivalencia ya existe para este ingrediente y unidad
            existing_equivalence = UnitEquivalence.query.filter_by(
                ingredient_id=ingredient.id,
                household_unit=household_unit
            ).first()

            if existing_equivalence:
                print(f"Equivalencia para '{ingredient_name}' - '{household_unit}' ya existe. Saltando.")
                count_skipped_already_exists += 1
                continue

            # Crear y guardar la nueva equivalencia
            new_equivalence = UnitEquivalence(
                ingredient_id=ingredient.id,
                household_unit=household_unit,
                grams_per_unit=float(grams_per_unit)
            )
            db.session.add(new_equivalence)
            count_added += 1
            print(f"Añadiendo equivalencia: {ingredient.name} - 1 {household_unit} = {grams_per_unit}g")

        try:
            db.session.commit()
            print("\n--- Resumen ---")
            print(f"Equivalencias añadidas exitosamente: {count_added}")
            print(f"Equivalencias omitidas (ingrediente no encontrado): {count_skipped_ingredient_not_found}")
            print(f"Equivalencias omitidas (ya existían): {count_skipped_already_exists}")
        except Exception as e:
            db.session.rollback()
            print(f"Ocurrió un error durante el commit final: {e}")

if __name__ == '__main__':
    populate_equivalences()
    print("\nScript de población de equivalencias finalizado.")
