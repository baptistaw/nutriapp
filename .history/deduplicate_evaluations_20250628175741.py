from app import app, db, Patient, Evaluation
def remove_duplicate_evaluations():
    with app.app_context():
        duplicates_removed = 0
        patients = Patient.query.all()
        for patient in patients:
            evaluations = (
                patient.evaluations.order_by(Evaluation.consultation_date.asc()).all()
            )
            seen_dates = {}
            for ev in evaluations:
                date_key = ev.consultation_date.date() if ev.consultation_date else None
                if date_key in seen_dates:
                    score = ev.calculated_imc if ev.calculated_imc is not None else ev.weight_at_eval
                    app.logger.debug(
                        f"DUPLICATE_EVALUATION_REMOVED: {patient.name} {patient.surname} - {ev.consultation_date.strftime('%Y-%m-%d')} - {score}"
                    )
                    db.session.delete(ev)
                    duplicates_removed += 1
                else:
                    seen_dates[date_key] = ev
        db.session.commit()
        print(f"Duplicados eliminados: {duplicates_removed}")


if __name__ == "__main__":
    remove_duplicate_evaluations()
