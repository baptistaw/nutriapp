/**
 * Obtiene el valor numérico de un elemento del DOM por su ID.
 * @param {string} id - El ID del elemento.
 * @returns {number|null} El valor numérico o null si está vacío, no se encuentra el elemento o no es un número.
 */
function getNumber(id) {
  const element = document.getElementById(id);
  if (!element) {
    console.warn(`Elemento con ID '${id}' no encontrado para getNumber.`);
    return null;
  }
  const value = element.value;
  if (value === "" || value === null || value === undefined) return null;
  const num = parseFloat(value);
  return isNaN(num) ? null : num;
}

/**
 * Obtiene una lista de strings de un elemento del DOM por su ID,
 * asumiendo que el valor es una cadena separada por comas.
 * Útil para campos Tagify que actualizan el valor del input original.
 * @param {string} id - El ID del elemento input.
 * @returns {string[]} Un array de strings, o un array vacío si no hay valor o el elemento no se encuentra.
 */
function getList(id) {
  const element = document.getElementById(id);
  if (element && typeof element.value === 'string' && element.value.trim() !== '') {
    // Primero, intenta parsear como JSON si es un array de objetos Tagify
    try {
        const tags = JSON.parse(element.value);
        if (Array.isArray(tags)) {
            return tags.map(tag => tag.value.trim()).filter(s => s);
        }
    } catch (e) {
        // Si no es JSON, o el parseo falla, trata como string separado por comas
        // console.warn(`Valor para ${id} no es un JSON de Tagify, tratando como CSV. Valor:`, element.value);
    }
    return element.value.split(',').map(s => s.trim()).filter(s => s);
  }

  return [];
}
/**---------------- Globals ----------------------*/
let currentPlanDataBaseData = null;
let currentEvaluationId = null;
let lastCalculatedReferences = {}; // Para almacenar las referencias de la última llamada a calcularValores
window.selectedRelevantPreparations = []; // Inicializar la variable global

/**------------------- UI Helpers ----------------------*/
function setupTechnicalPdfButton(evaluationId) {
  const techPdfButton = document.getElementById("viewTechnicalPdfButton");
  if (!techPdfButton) {
    console.warn("WARN: Botón viewTechnicalPdfButton no encontrado.");
    return;
  }
  if (evaluationId) {
    const pdfUrl = `/ver_pdf/${evaluationId}`;
    techPdfButton.onclick = () => {
      console.log(`INFO: Abriendo ${pdfUrl}`);
      window.open(pdfUrl, "_blank");
    };
    techPdfButton.style.display = "inline-block";
  } else {
    techPdfButton.style.display = "none";
    techPdfButton.onclick = null;
  }
}

/**
 * Configura la visibilidad y los handlers de los botones relacionados con una evaluación guardada (PDFs, Email).
 * @param {string|number|null} evaluationId - El ID de la evaluación. Si es null, oculta los botones.
 */
function setupPatientPdfButton(evaluationId) {
  const patientPdfButton = document.getElementById("viewPatientPdfButton");
  const sendEmailButton = document.getElementById("sendEmailToPatientButton");

  if (evaluationId) {
    if (patientPdfButton) {
      const pdfUrl = `/ver_pdf_paciente/${evaluationId}`;
      patientPdfButton.onclick = () => {
        console.log(`INFO: Abriendo PDF paciente ${pdfUrl}`);
        window.open(pdfUrl, "_blank");
      };
      patientPdfButton.style.display = "inline-block";
    } else {
      console.warn("WARN: Botón viewPatientPdfButton no encontrado.");
    }

    if (sendEmailButton) {
      sendEmailButton.onclick = () => sendPlanByEmail(evaluationId);
      sendEmailButton.style.display = "inline-block";
    } else {
      console.warn("WARN: Botón sendEmailToPatientButton no encontrado.");
    }
  } else {
    if (patientPdfButton) {
      patientPdfButton.style.display = "none";
      patientPdfButton.onclick = null;
    }
    if (sendEmailButton) {
      sendEmailButton.style.display = "none";
      sendEmailButton.onclick = null;
    }
  }
}

function showFinalMessage(message, type = "info", autoHide = true) {
  const finalMessageDiv = document.getElementById("final-message");
  if (!finalMessageDiv) return;
  finalMessageDiv.innerHTML = "";
  finalMessageDiv.textContent = message;
  finalMessageDiv.className = `mt-3 alert alert-${type} alert-dismissible fade show`;
  finalMessageDiv.style.display = "block";
  if (!finalMessageDiv.querySelector(".btn-close")) {
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "btn-close";
    closeButton.setAttribute("data-bs-dismiss", "alert");
    closeButton.setAttribute("aria-label", "Close");
    closeButton.onclick = () => {
      finalMessageDiv.style.display = "none";
    };
    finalMessageDiv.appendChild(closeButton);
  }
  if (autoHide) {
    setTimeout(() => {
      try {
        const alertInstance = bootstrap.Alert.getOrCreateInstance(finalMessageDiv);
        if (alertInstance) alertInstance.close();
        else finalMessageDiv.style.display = "none";
      } catch (e) {
        finalMessageDiv.style.display = "none";
      }
    }, 7000);
  }
}

function hideFinalMessage() {
  const finalMessageDiv = document.getElementById("final-message");
  if (finalMessageDiv) {
    finalMessageDiv.style.display = "none";
  }
}

/**
 * Limpia el formulario y el estado de búsqueda.
 * @param {boolean} isInitialLoad - Indica si la limpieza es parte de la carga inicial de la página.
 */
function clearFormAndSearch(isInitialLoad = false) {
  console.log("INFO: Limpiando formulario y estado...");

  // 1) Reset del formulario principal
  const form = document.getElementById("patient-plan-form"); // Asegúrate de que este ID exista
  if (form) {
    form.reset();
  }

  // 2) Reset de valores calculados
  const calculatedIds = [
    "calculated-complexion",
    "calculated-ideal-weight",
    "calculated-get",
    "calculated-imc",
    "calculated-whr",
    "calculated-whtr"
  ];
  calculatedIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      // Para peso ideal, mantenemos la unidad
      el.textContent = id === "calculated-ideal-weight" ? "-- kg" : "--";
      // Reiniciamos clases de color (si quieres)
      el.className = "value-display risk-none";
    }
  });

  // 3) Reset de checkboxes
  document.querySelectorAll('input[name="pathologies"]').forEach(cb => {
    cb.checked = false;
  });

  // 4) Ocultar sección de plan generado y resetear su contenido
  const planSection = document.getElementById("plan-display-section");
  if (planSection) {
    planSection.style.display = "none";
  }
  const planText = document.getElementById("planTextArea");
  if (planText) {
    planText.value = "";
  }
  const obs = document.getElementById("user_observations");
  if (obs) {
    obs.value = "";
  }

  // 5) Ocultar mensaje final
  hideFinalMessage();

  // 6) Reset de búsqueda
  const searchMsg = document.getElementById("search-message");
  if (searchMsg) {
    searchMsg.textContent = "Ingrese término y pulse Buscar.";
  }
  const listContainer = document.getElementById("patient-list-container");
  if (listContainer) {
    listContainer.style.display = "none";
  }
  const listUl = document.getElementById("patient-select-list");
  if (listUl) {
    listUl.innerHTML = "";
  }
  const histContainer = document.getElementById("evaluation-history-container");
  if (histContainer) {
    histContainer.style.display = "none";
  }
  const histSelect = document.getElementById("evaluation-select");
  if (histSelect) {
    histSelect.innerHTML = '<option value="">-- Nueva evaluación o seleccionar historial --</option>';
  }

  // 7) Reset de botón de imprimir / estado global
  setupTechnicalPdfButton(null);
  setupPatientPdfButton(null); // Esto ahora también maneja el botón de email
  currentPlanDataBaseData = null;
  lastCalculatedReferences = {}; // Resetear referencias cacheadas
  currentEvaluationId = null;

  // 8) Reset de campos ocultos y búfer de búsqueda
  const hid1 = document.getElementById("patient_id");
  if (hid1) hid1.value = "";
  const hid2 = document.getElementById("loaded_evaluation_id");
  if (hid2) hid2.value = "";
  const searchInput = document.getElementById("search_query");
  if (searchInput) searchInput.value = "";

  // 9) Limpiar contenedor de preparaciones relevantes
  const relevantPreparationsContainer = document.getElementById('relevant-preparations-container');
  if (relevantPreparationsContainer) {
    relevantPreparationsContainer.innerHTML = '<p id="relevant-preparations-placeholder">Haz clic en el botón para buscar preparaciones.</p>';
  }
  window.selectedRelevantPreparations = []; // Limpiar las preparaciones seleccionadas
  // 10) Limpiar mensaje de error de validación
  displayValidationErrorInForm("");
}

/**
 * Envía el plan del paciente por email.
 * @param {string|number} evaluationId - El ID de la evaluación a enviar.
 */
async function sendPlanByEmail(evaluationId) {
    console.log(`****** INICIO sendPlanByEmail() para evaluación ID: ${evaluationId} ******`);
    if (!evaluationId) {
        alert("No hay una evaluación guardada para enviar por email.");
        console.warn("WARN: sendPlanByEmail llamado sin evaluationId.");
        return;
    }

    const emailButton = document.getElementById("sendEmailToPatientButton");
    const originalButtonHtml = emailButton ? emailButton.innerHTML : null;

    if (emailButton) {
        emailButton.disabled = true;
        emailButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Enviando...';
    }
    showFinalMessage("Enviando plan por email...", "info", false); // No auto-ocultar este mensaje

    try {
        const response = await fetch(`/enviar_plan_por_email/${evaluationId}`, {
            method: 'POST', // O GET, según tu implementación de backend
            headers: { 'Content-Type': 'application/json' },
            // body: JSON.stringify({ some_optional_data: "value" }) // Si necesitas enviar datos en el cuerpo
        });

        const result = await response.json();

        if (!response.ok) {
            console.error("ERROR: Respuesta no OK de /enviar_plan_por_email. Status:", response.status, "Status Text:", response.statusText);
            let errorDetail = `Error del servidor: ${response.status} ${response.statusText}`;
            const contentType = response.headers.get('Content-Type');

            if (contentType && contentType.includes('application/json')) {
                // Si es JSON, intenta parsearlo para más detalles
                const errorData = await response.json().catch(() => ({ error: 'No se pudo parsear el JSON de error.' }));
                errorDetail = errorData.error || errorDetail;
                console.error("ERROR: Respuesta JSON de error:", errorData);
            } else {
                // Si no es JSON (como HTML para 404), obtén el texto de la respuesta (opcional, para depuración)
                const errorText = await response.text().catch(() => 'No se pudo obtener el texto de la respuesta.');
                console.error("ERROR: Respuesta no JSON (primeros 200 chars):", errorText.substring(0, 200) + '...');
            }
            throw new Error(errorDetail); // Lanza el error con el detalle recolectado
        }

        console.log("INFO: Respuesta exitosa de /enviar_plan_por_email:", result);
    } catch (error) {
        console.error("ERROR en sendPlanByEmail:", error);
        showFinalMessage(`Error al enviar email: ${error.message || 'Error desconocido.'}`, "danger");
    } finally {
        if (emailButton) {
            emailButton.disabled = false;
            emailButton.innerHTML = originalButtonHtml || '<i class="fas fa-envelope"></i> Enviar Plan por Email';
        }
    }
}

/**
 * Muestra un mensaje de error de validación en el formulario.
 * @param {string} message - El mensaje de error a mostrar. Si está vacío, oculta el contenedor.
 */
function displayValidationErrorInForm(message) {
  const errorContainer = document.getElementById('validation-error-container'); // Asegúrate de tener este div en tu HTML
  if (errorContainer) {
    errorContainer.textContent = message;
    errorContainer.style.display = message ? 'block' : 'none'; // Mostrar u ocultar
    if (message) {
      errorContainer.className = 'alert alert-danger mt-2'; // Clases de Bootstrap para alerta
    } else {
      errorContainer.className = ''; // Limpiar clases si no hay mensaje
    }
    // Si se muestra un mensaje, hacer scroll hacia él
    if (message && errorContainer.style.display === 'block') {
      errorContainer.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }
}

/**
 * Limpia los campos calculados en la UI cuando ocurre un error.
 */
function clearCalculatedFieldsOnError() {
  const idsToClear = ["calculated-imc", "calculated-complexion", "calculated-ideal-weight", "calculated-get", "calculated-whr", "calculated-whtr"];
  idsToClear.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.textContent = (id === "calculated-ideal-weight") ? "-- kg" : "--";
      el.className = "value-display risk-none"; // Resetear clase de riesgo
    }
  });
  // También podrías querer limpiar los contenedores de rangos (imc-ranges, whr-ranges, whtr-ranges)
  ['imc-ranges', 'whr-ranges', 'whtr-ranges'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });
}

/**---------------- Backend Interaction ------------------*/
async function buscarPaciente() {
  // tu implementación...
}
function displayPatientDataAndHistory(li) {
  // tu implementación...
}
async function loadEvaluationData(id) {
  // tu implementación...
}

/**
 * Calcula valores derivados automáticamente.
 */
async function calcularValores(event) {
  console.log(`****** INICIO calcularValores() ******`);
  const idsToFetch = {
    height: "height_cm",
    weight: "weight_at_plan",
    wrist: "wrist_circumference_cm",
    waist: "waist_circumference_cm",
    hip: "hip_circumference_cm",
    dob: "dob",
    sex: "sex",
    activity_factor: "activity_factor"
  };
  const data = {};
  console.log("INFO: Iniciando recolección de datos para calcularValores...");
  for (const k in idsToFetch) {
    const element = document.getElementById(idsToFetch[k]);
    let v = element ? element.value : null;
    let originalValue = v; // Guardar valor original para logging

    if (element) {
        if (["height", "weight", "wrist", "waist", "hip", "activity_factor"].includes(k)) {
          v = parseFloat(v);
          if (isNaN(v) || v <= 0) v = null; // Si no es un número positivo, se considera null
        }
        data[k] = v || null; // Si v es una cadena vacía, también se convierte en null aquí
        console.log(`INFO: Campo procesado: ${k} (ID: ${idsToFetch[k]}), Original: "${originalValue}", Final en data: ${data[k]}`);
    } else {
        data[k] = null;
        console.warn(`WARN: Elemento no encontrado para ${k} (ID: ${idsToFetch[k]}). Establecido a null.`);
    }
  }

  // Validar datos mínimos
  if (["height", "weight", "dob", "sex", "activity_factor"].some(f => !data[f])) {
    console.warn("WARN: Faltan datos requeridos para calcularValores. Saliendo de la función. Datos actuales:", JSON.parse(JSON.stringify(data)));
    // Podrías mostrar un mensaje al usuario aquí también si lo deseas
    // displayValidationErrorInForm("Faltan datos básicos (altura, peso, fecha de nacimiento, sexo, factor de actividad) para realizar los cálculos.");
    // clearCalculatedFieldsOnError(); // Limpiar campos si faltan datos básicos
    return;
  }
  console.log("INFO: Datos recolectados y validados para calcularValores:", JSON.parse(JSON.stringify(data)));

  // Reset UI antes de la llamada
  ["calculated-imc","calculated-ideal-weight","calculated-get",
   "calculated-complexion","calculated-whr","calculated-whtr"
  ].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = id==="calculated-ideal-weight" ? "-- kg" : "--";
  });

  try {
    console.log("INFO: Enviando petición a /calcular_valores con datos:", JSON.parse(JSON.stringify(data)));
    const resp = await fetch("/calcular_valores", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data)
    });
    console.log("INFO: Respuesta recibida de /calcular_valores, status:", resp.status);
    
    if (!resp.ok) {
        // Intenta parsear el JSON de error
        const errorData = await resp.json().catch(() => ({ error: `Error del servidor: ${resp.status}. No se pudo obtener detalle.` }));
        console.error("ERROR: Respuesta no OK de /calcular_valores. Respuesta JSON:", errorData);
        throw new Error(errorData.error || `Error del servidor: ${resp.status}`);
    }
    
    const res = await resp.json(); // Parsear JSON solo si la respuesta es OK
    console.log("INFO: Datos JSON de respuesta de /calcular_valores:", JSON.parse(JSON.stringify(res)));

    // Si todo OK, limpiar cualquier mensaje de error previo
    displayValidationErrorInForm("");

    // IMC
    const imcEl = document.getElementById("calculated-imc");
    if (imcEl) {
      imcEl.textContent = res.imc?.toFixed(1) ?? "--";
      imcEl.className = `value-display risk-${res.imc_risk||"none"}`;
    }
    // Rangos IMC
    const imcRangesEl = document.getElementById("imc-ranges");
    if (imcRangesEl) {
      const refs = res.references?.imc || {};
      imcRangesEl.innerHTML = Object.entries(refs)
        .filter(([k]) => k!=="riesgo_actual")
        .map(([k,v]) => `<strong>${k.replace(/_/g," ")}:</strong> ${v}`)
        .join("<br>");
    }

    // Complexión
    const compEl = document.getElementById("calculated-complexion");
    if (compEl) compEl.textContent = res.complexion||"--";

    // Peso ideal
    const idealEl = document.getElementById("calculated-ideal-weight");
    if (idealEl) idealEl.textContent = (res.ideal_weight?.toFixed(1)||"--")+" kg";

    // GET
    const getEl = document.getElementById("calculated-get");
    if (getEl) getEl.textContent = res.get?.toFixed(0)||"--";

    // WHR
    const whrEl = document.getElementById("calculated-whr");
    if (whrEl) {
      whrEl.textContent = res.waist_hip_ratio?.toFixed(2)||"--";
      whrEl.className = `value-display risk-${res.whr_risk||"none"}`;
    }
    const whrRangesEl = document.getElementById("whr-ranges");
    if (whrRangesEl) {
      const refs = res.references?.whr || {};
      whrRangesEl.innerHTML = Object.entries(refs)
        .filter(([k]) => k!=="riesgo_actual")
        .map(([k,v]) => `<strong>${k.replace(/_/g," ")}:</strong> ${v}`)
        .join("<br>");
    }

    // WHTR
    const whtrEl = document.getElementById("calculated-whtr");
    if (whtrEl) {
      whtrEl.textContent = res.waist_height_ratio?.toFixed(2)||"--";
      whtrEl.className = `value-display risk-${res.whtr_risk||"none"}`;
    }
    const whtrRangesEl = document.getElementById("whtr-ranges");
    if (whtrRangesEl) {
      const refs = res.references?.whtr || {};
      whtrRangesEl.innerHTML = Object.entries(refs)
        .filter(([k]) => k!=="riesgo_actual")
        .map(([k,v]) => `<strong>${k.replace(/_/g," ")}:</strong> ${v}`)
        .join("<br>");
    }
    // Almacenar las referencias globalmente para usarlas en generarPlan
    lastCalculatedReferences = res.references || {};
    console.log("INFO: Referencias almacenadas desde calcularValores:", JSON.parse(JSON.stringify(lastCalculatedReferences)));

  } catch (err) {
    console.error("ERROR en calcularValores (durante fetch o procesamiento de respuesta):", err);
    displayValidationErrorInForm(err.message || "Error desconocido al calcular valores.");
    // Limpiar referencias si hubo error
    lastCalculatedReferences = {};
    console.log("INFO: Referencias limpiadas debido a error en calcularValores.");
    // Limpiar campos calculados en la UI
    clearCalculatedFieldsOnError();
  }
}

/**
 * Carga y muestra preparaciones relevantes para el paciente actual.
 */
async function loadRelevantPreparationsForPatient() {
  const relevantPreparationsContainer = document.getElementById('relevant-preparations-container');
  const placeholder = document.getElementById('relevant-preparations-placeholder');
  if (!relevantPreparationsContainer || !placeholder) {
    console.error("Contenedores para preparaciones relevantes no encontrados.");
    return;
  }

  placeholder.textContent = 'Buscando preparaciones relevantes...';
  relevantPreparationsContainer.innerHTML = ''; // Limpiar resultados anteriores
  relevantPreparationsContainer.appendChild(placeholder);

  // Recolectar datos del paciente para enviar al backend
  // Principalmente patologías, pero podrías añadir otros criterios si tu API los soporta
  const patientPathologies = Array.from(document.querySelectorAll('input[name="pathologies"]:checked')).map(cb => cb.value);
  const patientAversions = getList('aversions'); 
  const patientAllergies = getList('allergies'); // Recolectar alergias
  const patientIntolerances = getList('intolerances'); // Recolectar intolerancias
  
  // Aquí podrías añadir más datos si tu endpoint /api/relevant_preparations_for_patient los usa
  const patientDataForFiltering = {
    pathologies: patientPathologies,
    aversions: patientAversions,
    allergies: patientAllergies,         // Incluir alergias
    intolerances: patientIntolerances  // Incluir intolerancias
    // Ejemplo: objective_low_cholesterol: document.getElementById('some_cholesterol_objective_checkbox')?.checked // Mantener ejemplo
  };

  try {
    const response = await fetch('/api/relevant_preparations_for_patient', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patientDataForFiltering)
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ error: `Error del servidor: ${response.status}` }));
      throw new Error(errorData.error || `Error ${response.status} al cargar preparaciones relevantes.`);
    }

    const preparations = await response.json();
    placeholder.style.display = 'none'; // Ocultar placeholder

    if (preparations.length === 0) {
      relevantPreparationsContainer.innerHTML = '<p class="text-muted">No se encontraron preparaciones favoritas relevantes para los criterios actuales del paciente.</p>';
      return;
    }

    preparations.forEach(prep => {
      const div = document.createElement('div');
      div.classList.add('form-check', 'mb-2');
      div.innerHTML = `
        <input class="form-check-input relevant-preparation-checkbox" type="checkbox" value="${prep.id}" id="prep_fav_${prep.id}" data-preparation='${JSON.stringify(prep)}'>
        <label class="form-check-label" for="prep_fav_${prep.id}">
          <strong>${prep.name}</strong> (${prep.preparation_type || 'N/A'}) - Tags: <em>${(prep.suitability_tags || []).join(', ') || 'Ninguno'}</em>
        </label>
      `;
      relevantPreparationsContainer.appendChild(div);

      // Dentro de loadRelevantPreparationsForPatient, después de crear el checkbox:
      const checkboxInput = div.querySelector('.relevant-preparation-checkbox');
      checkboxInput.addEventListener('change', function() {
          const prepData = JSON.parse(this.dataset.preparation);
          window.selectedRelevantPreparations = window.selectedRelevantPreparations || [];
          if (this.checked) {
              // Añadir si no está ya (por si acaso)
              if (!window.selectedRelevantPreparations.find(p => p.id === prepData.id)) {
                  window.selectedRelevantPreparations.push(prepData);
              }
          } else {
              // Eliminar
              window.selectedRelevantPreparations = window.selectedRelevantPreparations.filter(p => p.id !== prepData.id);
          }
          console.log("INFO: window.selectedRelevantPreparations actualizado:", window.selectedRelevantPreparations);
      });
    }); // Fin de forEach
  } // ESTA ES LA LLAVE DE CIERRE DEL BLOQUE 'try' ANTES DEL 'catch'
    catch (error) {
    console.error("Error en loadRelevantPreparationsForPatient:", error);
    relevantPreparationsContainer.innerHTML = `<p class="text-danger">Error al cargar preparaciones: ${error.message}</p>`;
    placeholder.style.display = 'none';
  }

} // ESTA ES LA LLAVE DE CIERRE DEL BLOQUE 'try' ANTES DEL 'catch'
// Corregido: Asegurar que no haya 'a' extra y que la función esté bien definida.
async function generarPlan() { // Línea 590 en tu log
  console.log("%c!!!!!! generarPlan function CALLED !!!!!!", "color: green; font-weight: bold; font-size: 1.2em;"); // Diagnostic log
  console.log("****** INICIO generarPlan() ******");
  hideFinalMessage();
  currentPlanDataBaseData = null;
  // No reseteamos lastCalculatedReferences aquí, ya que debe venir de la última ejecución de calcularValores

  // Recolectar micro:
  const micronutrients = {
    potassium_mg: getNumber('mic_k'),
    calcium_mg: getNumber('mic_ca'),
    sodium_mg: getNumber('mic_na'),
    cholesterol_mg: getNumber('mic_chol'),
  };


  // Nueva lógica para base_foods que se enviará al backend:
  let baseFoodsForPayload = [];

  // 1. Incluir UserPreparations detalladas si están seleccionadas
  if (window.selectedRelevantPreparations && Array.isArray(window.selectedRelevantPreparations) && window.selectedRelevantPreparations.length > 0) {
    window.selectedRelevantPreparations.forEach(prep => {
      if (prep && typeof prep === 'object' && prep.name && Array.isArray(prep.ingredients)) {
        baseFoodsForPayload.push({
          name: prep.name,
          original_ingredients: prep.ingredients.map(ing => ({ item: ing.item })) // Solo el nombre del item
        });
      }
    });
  }

  // 2. Incluir strings simples del campo Tagify 'base_foods'
  const baseFoodsFromTagify = getList('base_foods'); // getList devuelve un array de strings
  baseFoodsFromTagify.forEach(foodName => {
    if (!baseFoodsForPayload.some(bf => typeof bf === 'object' && bf.name === foodName)) {
      baseFoodsForPayload.push(foodName); // Añadir como string simple si no existe ya como objeto detallado
    }
  });

  const pathologies = Array.from(document.querySelectorAll('input[name="pathologies"]:checked')).map(
    (cb) => cb.value
  );

  const planBaseData = {
    patient_id: document.getElementById("patient_id").value || null,
    name: document.getElementById("name").value.trim(),
    surname: document.getElementById("surname").value.trim(),
    cedula: document.getElementById("cedula").value.trim(),
    dob: document.getElementById("dob").value,
    sex: document.getElementById("sex").value,
    email: document.getElementById("email").value.trim(),
    phone_number: document.getElementById("phone_number").value.trim(),
    education_level: document.getElementById("education_level").value,
    purchasing_power: document.getElementById("purchasing_power").value,
    height_cm: parseFloat(document.getElementById("height_cm").value) || null,
    weight_at_plan: parseFloat(document.getElementById("weight_at_plan").value) || null,
    wrist_circumference_cm:
      parseFloat(document.getElementById("wrist_circumference_cm").value) || null,
    waist_circumference_cm:
      parseFloat(document.getElementById("waist_circumference_cm").value) || null,
    hip_circumference_cm:
      parseFloat(document.getElementById("hip_circumference_cm").value) || null,
    gestational_age_weeks:
      parseInt(document.getElementById("gestational_age_weeks").value, 10) || 0,
    activity_factor: parseFloat(document.getElementById("activity_factor").value) || null,
    pathologies,
    other_pathologies_text: document.getElementById("other_pathologies_text").value.trim(),
    postoperative_text: document.getElementById("postoperative_text").value.trim(),
    allergies: getList("allergies"),
    intolerances: getList("intolerances"),
    preferences: getList("preferences"),
    aversions: getList("aversions"),
    diet_type: document.getElementById("diet_type").value,
    other_diet_type_text: document.getElementById("other_diet_type_text").value.trim(),
    target_weight: parseFloat(document.getElementById("target_weight").value) || null,
    target_waist_cm: parseFloat(document.getElementById("target_waist_cm").value) || null,
    target_protein_perc:
      parseFloat(document.getElementById("target_protein_perc").value) || null,
    target_carb_perc:
      parseFloat(document.getElementById("target_carb_perc").value) || null,
    target_fat_perc: parseFloat(document.getElementById("target_fat_perc").value) || null,
    micronutrients,
    base_foods: baseFoodsForPayload,
    references: lastCalculatedReferences
  };

  const required = [
    "name",
    "surname",
    "cedula",
    "dob",
    "sex",
    "height_cm",
    "weight_at_plan",
    "activity_factor",
    "target_weight"
  ];
  const missing = required.filter((f) => !planBaseData[f]);
  if (missing.length) {
    alert(`Complete los campos: ${missing.join(", ")}`);
    return;
  }

  const sum =
    (planBaseData.target_protein_perc || 0) +
    (planBaseData.target_carb_perc || 0) +
    (planBaseData.target_fat_perc || 0);
  if (sum > 0 && (sum < 98 || sum > 102)) {
    alert("La suma de macros debe ser ≈100% o todos en 0.");
    return;
  }

  document.getElementById("plan-display-section").style.display = "block";
  const textarea = document.getElementById("planTextArea");
  textarea.value = "Generando plan con IA... Por favor, espere.";
  textarea.disabled = true;

  try {
    const resp = await fetch("/generar_plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(planBaseData)
    });
    const res = await resp.json(); // 'res' es la respuesta JSON del backend
    if (!resp.ok) throw new Error(res.error || `Error del servidor: ${resp.status}`);
    
    console.log("INFO: Respuesta completa de /generar_plan (Flask):", JSON.parse(JSON.stringify(res))); 

    textarea.value = res.gemini_raw_text || "Error: plan inválido recibido del servidor.";
    currentPlanDataBaseData = res.plan_data_for_save;
    console.log("INFO: currentPlanDataBaseData (que se usará para finalizar) después de /generar_plan:", JSON.parse(JSON.stringify(currentPlanDataBaseData)));

    // --- Lógica para parsear y mostrar recetas para favoritear ---
    const fullPlanText = res.gemini_raw_text;
    const recipeSectionMarker = "== RECETARIO DETALLADO ==";
    const parts = fullPlanText.split(recipeSectionMarker);
    const recipesDetailedText = parts.length > 1 ? parts[1].trim() : "";

    console.log("FRONTEND - Texto COMPLETO del recetario para parsear en JS:\n", recipesDetailedText);
    console.log("FRONTEND - recipesDetailedText length:", recipesDetailedText.length);

    const recipesContainer = document.getElementById('favoriteRecipesContainer');
    const favoriteSelectionContainer = document.getElementById('favorite-recipes-selection-container');
    
    if (!recipesContainer || !favoriteSelectionContainer) {
      console.error("Error: No se encontraron los contenedores 'favoriteRecipesContainer' o 'favorite-recipes-selection-container'. La funcionalidad de favoritear recetas no estará disponible.");
    } else {
        recipesContainer.innerHTML = ''; // Limpiar contenedor antes de añadir nuevos

        if (recipesDetailedText) {
          const recipeMatches = [];
          // Regex para dividir recipesDetailedText en bloques completos de recetas individuales
          const recipeStartPattern = /\n?(Receta\s*(?:N°|No\.|N\.)?\s*\d+:\s*[\s\S]+?)(?=\nReceta\s*(?:N°|No\.|N\.)?\s*\d+:|$)/gi;
          let match;

          while ((match = recipeStartPattern.exec(recipesDetailedText)) !== null) {
            if (match[1]) { // match[1] es el grupo de captura que contiene el bloque completo de la receta.
              recipeMatches.push(match[1].trim());
            }
          }
          console.log("FRONTEND - Recetas individuales matcheadas por Regex:", recipeMatches);
          console.log("FRONTEND - Número de recetas matcheadas:", recipeMatches.length);

          if (recipeMatches.length > 0) {
            favoriteSelectionContainer.style.display = 'block'; // Mostrar el contenedor de selección
            recipeMatches.forEach((fullRecipeBlock, index) => {
              let recipeTitle = null;
              // Intento 1: Extraer título que no contenga saltos de línea, deteniéndose antes de "Rinde:" o "Porciones que Rinde:"
              const titleMatchAttempt1 = fullRecipeBlock.match(/^(Receta\s*(?:N°|No\.|N\.)?\s*\d+:\s*[^\n]+?)(?=\s*\n\s*(?:Rinde:|Porciones que Rinde:)|$)/i);
              if (titleMatchAttempt1 && titleMatchAttempt1[1]) {
                recipeTitle = titleMatchAttempt1[1].trim();
              } else {
                // Intento 2 (Fallback): Tomar la primera línea que comience con "Receta..."
                const titleMatchAttempt2 = fullRecipeBlock.match(/^(Receta\s*(?:N°|No\.|N\.)?\s*\d+:\s*[^\n]+)/i);
                if (titleMatchAttempt2 && titleMatchAttempt2[0]) {
                  recipeTitle = titleMatchAttempt2[0].trim();
                }
              }
              console.log(`FRONTEND - Bloque ${index} - Título extraído: '${recipeTitle}' --- Del Bloque (primeros 200 chars): ${fullRecipeBlock.substring(0,200)}...`);
              if (recipeTitle) {
                const checkboxId = `fav_recipe_js_${index}`;
                const div = document.createElement('div');
                div.className = 'form-check mb-2';
                div.innerHTML = `
                  <input class="form-check-input" type="checkbox" value="${recipeTitle}" id="${checkboxId}" name="favorite_recipes_frontend">
                  <label class="form-check-label" for="${checkboxId}">
                    ${recipeTitle}
                  </label>
                `;
                recipesContainer.appendChild(div);
              } else {
                console.warn("FRONTEND - No se pudo extraer un título válido para el bloque de receta:", fullRecipeBlock.substring(0, 200));
              }
            });
          } else {
            recipesContainer.innerHTML = '<p class="text-muted">No se encontraron recetas detalladas en el formato esperado para seleccionar.</p>';
            favoriteSelectionContainer.style.display = 'none';
            console.log("FRONTEND - No se encontraron bloques de recetas con el patrón global en recipesDetailedText.");
          }
        } else {
          recipesContainer.innerHTML = '<p class="text-muted">La sección de recetas detalladas está vacía.</p>';
          favoriteSelectionContainer.style.display = 'none';
          console.log("FRONTEND - recipesDetailedText está vacío.");
        }
    }
    // Fin de la lógica de parseo de recetas

  } catch (err) {
    console.error("ERROR en generarPlan (fetch a /generar_plan o parseo de recetas):", err);
    // Asegurarse de que textarea esté definido antes de intentar acceder a su valor
    if (textarea) textarea.value = `Error al generar el plan: ${err.message}`;
    showFinalMessage(`Error al generar el plan: ${err.message}`, "danger");
  } finally {
    if (textarea) textarea.disabled = false;
  }
}

// Definir la variable watch aquí si es una lista estática de IDs
// o asegurarse de que se defina antes de que main.js la use.
const watch = ["height_cm", "weight_at_plan", "wrist_circumference_cm", "waist_circumference_cm", "hip_circumference_cm", "dob", "sex", "activity_factor"];

async function finalizar() {
  console.log("****** INICIO finalizar() ******");
  hideFinalMessage();

  const planTextArea = document.getElementById("planTextArea");
  const editedPlanText = planTextArea ? planTextArea.value : null;

  if (!currentPlanDataBaseData) {
    showFinalMessage("Error: No hay datos base del plan para guardar. Por favor, genera un plan primero.", "danger");
    console.error("ERROR en finalizar(): currentPlanDataBaseData es null.");
    return;
  }

  const favoriteRecipeCheckboxes = document.querySelectorAll('input[name="favorite_recipes_frontend"]:checked');
  const selectedFavoriteRecipes = Array.from(favoriteRecipeCheckboxes).map(cb => cb.value);

  const dataToSave = {
    ...currentPlanDataBaseData,
    edited_plan_text: editedPlanText,
    user_observations: document.getElementById("user_observations")?.value || "",
    selected_favorite_recipes: selectedFavoriteRecipes,
    loaded_evaluation_id: document.getElementById("loaded_evaluation_id")?.value || null
  };

  console.log("INFO: Datos a enviar para finalizar/guardar:", JSON.parse(JSON.stringify(dataToSave)));

  const saveButton = document.querySelector('button[onclick="finalizar()"]');
  let originalButtonHtml = null;
  if (saveButton) {
     originalButtonHtml = saveButton.innerHTML;
     saveButton.disabled = true;
     saveButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Guardando...';
  }

  try {
    const endpoint = dataToSave.loaded_evaluation_id ? `/actualizar_evaluacion/${dataToSave.loaded_evaluation_id}` : "/guardar_evaluacion";
    const method = dataToSave.loaded_evaluation_id ? "PUT" : "POST";

    const response = await fetch(endpoint, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dataToSave)
    });

    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || `Error del servidor: ${response.status}`);
    }

    showFinalMessage(result.message || "Plan guardado exitosamente.", "success");
    console.log("INFO: Respuesta de guardar_evaluacion:", result);

    if (result.evaluation_id) {
      currentEvaluationId = result.evaluation_id;
      document.getElementById("loaded_evaluation_id").value = result.evaluation_id;
      setupTechnicalPdfButton(result.evaluation_id);
      setupPatientPdfButton(result.evaluation_id);
    }

  } catch (error) {
    console.error("ERROR en finalizar():", error);
    showFinalMessage(`Error al guardar el plan: ${error.message}`, "danger");
  } finally {
     if (saveButton) {
         saveButton.disabled = false;
         saveButton.innerHTML = originalButtonHtml || "Guardar Plan";
     }
  }
}

// ────────────────────────────────────────────────────────
// Listener DOMContentLoaded
// ────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {

  function validateClientSideField(inputElement) {
    if (!inputElement || inputElement.type !== 'number') return true;
    const fieldLabel = (inputElement.labels && inputElement.labels.length > 0
      ? inputElement.labels[0].textContent.replace(':', '').trim()
      : null) || inputElement.name || inputElement.id;
    const valueStr = inputElement.value.trim();
    if (valueStr === "") return true;
    const value = parseFloat(valueStr);
    if (isNaN(value)) {
      displayValidationErrorInForm(`El valor para '${fieldLabel}' debe ser un número.`);
      return false;
    }
    const min = parseFloat(inputElement.min), max = parseFloat(inputElement.max);
    if (!isNaN(min) && value < min) {
      displayValidationErrorInForm(`El valor para '${fieldLabel}' (${value}) no puede ser menor que ${min}.`);
      return false;
    }
    if (!isNaN(max) && value > max) {
      displayValidationErrorInForm(`El valor para '${fieldLabel}' (${value}) no puede ser mayor que ${max}.`);
      return false;
    }
    return true;
  }

  // Asumiendo que 'watch' es un array de IDs definido globalmente o en un scope accesible.
  // Si 'watch' no está definido, esta parte podría causar un error.
  // if (typeof watch !== 'undefined' && Array.isArray(watch)) { // Comentado si watch se define arriba
  if (typeof watch !== 'undefined' && Array.isArray(watch)) { // Se restaura la condición para evitar errores si watch no está lista
    watch.forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener("change", e => { validateClientSideField(e.target); calcularValores(e); });
    });
  } else {
    console.warn("WARN: La variable 'watch' no está definida o no es un array. Los listeners de cambio para calcularValores no se configurarán.");
  }
  // } // Comentado si watch se define arriba
  const otherNumericFieldsToValidate = [
    "gestational_age_weeks","target_weight","target_waist_cm",
    "target_protein_perc","target_carb_perc","target_fat_perc",
    "mic_k","mic_ca","mic_na","mic_chol"
  ];
  otherNumericFieldsToValidate.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", e => validateClientSideField(e.target));
  });

  const btnSearch = document.getElementById("btn-search");
  if (btnSearch) btnSearch.addEventListener("click", buscarPaciente);
  const btnClear = document.getElementById("btn-clear");
  if (btnClear) btnClear.addEventListener("click", () => clearFormAndSearch(false));
  const btnLoadRelevantPreps = document.getElementById("btn-load-relevant-preparations");
  if (btnLoadRelevantPreps) btnLoadRelevantPreps.addEventListener("click", loadRelevantPreparationsForPatient);
  const btnGenerarPlanIA = document.getElementById("btn-generar-plan");
  if (btnGenerarPlanIA) btnGenerarPlanIA.addEventListener("click", generarPlan);

  const tagifyElements = [
    { selector: 'input[name="allergies"]', id: 'allergies' },
    { selector: 'input[name="intolerances"]', id: 'intolerances' },
    { selector: 'input[name="preferences"]', id: 'preferences' },
    { selector: 'input[name="aversions"]', id: 'aversions' },
    { selector: 'input[name="base_foods"]', id: 'base_foods' }
  ];
  window.tagifyInstances = window.tagifyInstances || {};
  tagifyElements.forEach(item => {
    const input = document.querySelector(item.selector);
    if (input) {
      try {
        window.tagifyInstances[item.id] = new Tagify(input);
      } catch (e) {
        console.error(`Falló la inicialización de Tagify para ${item.id}:`, e);
      }
    }
  });

  clearFormAndSearch(true);
});