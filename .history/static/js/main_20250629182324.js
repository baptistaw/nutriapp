import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getAuth, signInWithEmailAndPassword, signOut, onAuthStateChanged, createUserWithEmailAndPassword, updateProfile } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

// --- Firebase Initialization ---
console.log("Firebase Config loaded:", window.firebaseConfig);
const app = initializeApp(window.firebaseConfig);
const auth = getAuth(app);

// --- Globals ---
let lastCalculatedReferences = {};
let currentPlanDataBaseData = null;
let patientChatIntervalId = null; // Variable global para el temporizador del chat del paciente

// --- Helper to check if it's a patient app route ---
function isPatientAppRoute(pathname) {
    // Patient portal routes start with '/patient/'.
    return pathname.startsWith('/patient/');
}

// --- Core Authentication Handler ---
// This is the single source of truth for handling authentication state changes.
async function handleAuthStateChange(user) {
  // Get UI elements
  const loginLink = document.getElementById('login-link');
  const registerLink = document.getElementById('register-link');
  const profileLink = document.getElementById('profile-link');
  const logoutLink = document.getElementById('logout-link');

  // Get current path info
  const currentPath = window.location.pathname;
   // CORRECCIÓN CRÍTICA: Usar '===' para evitar que '/patient/login' active esta lógica.
  const isNutritionistAuthPage = currentPath === '/login' || currentPath === '/register';
  const isPatientRoute = isPatientAppRoute(currentPath);

  if (user) { // User is authenticated with Firebase
    console.log("AUTH_STATE: Usuario logueado en Firebase:", user.uid);

    // Store token for API calls
    try {
      const idToken = await user.getIdToken(true); // Force refresh
      if (isPatientRoute) {
          localStorage.setItem('patientAuthToken', idToken);
      } else {
          localStorage.setItem('authToken', idToken);
      }
      console.log("AUTH_STATE: Firebase ID Token stored in localStorage.");
    } catch (error) {
      console.error("Error getting Firebase ID Token:", error);
      localStorage.removeItem('authToken');
      localStorage.removeItem('patientAuthToken');
    }

    // --- Logic for Patient App ---
    if (isPatientRoute) {
        if (currentPath === '/patient/login') {
            window.location.href = '/patient/dashboard';
            return;
        }
        return; // Patient pages don't need further auth handling here
    }

    // --- Logic for Nutritionist App ---
    if (!isPatientRoute) {
        // Hide/show appropriate links for nutritionist
        if (loginLink) loginLink.style.display = 'none';
        if (registerLink) registerLink.style.display = 'none';
        if (profileLink) profileLink.style.display = 'block';
        if (logoutLink) logoutLink.style.display = 'block';

        // FIX for redirect loop: If on a nutritionist auth page but logged into Firebase,
        // we need to log into the backend to create the Flask-Login session.
        if (isNutritionistAuthPage) {
            if (sessionStorage.getItem('autologin_attempted')) {
                console.error("LOOP DETECTADO Y DETENIDO: El inicio de sesión automático falló. Mostrando formulario manual.");
                const errorDiv = document.getElementById('login-error-message');
                if(errorDiv) {
                    errorDiv.textContent = "El inicio de sesión automático falló. Por favor, ingrese sus credenciales manualmente.";
                    errorDiv.classList.remove('d-none');
                }
            } else {
                sessionStorage.setItem('autologin_attempted', 'true');
                console.log("AUTH_STATE_CHANGE_DEBUG: Firebase user detected on nutritionist auth page. Attempting to log into backend automatically.");
                try {
                    const idToken = await user.getIdToken();
                    const form = document.createElement('form');
                    form.method = 'POST';
                    const nextUrl = new URLSearchParams(window.location.search).get('next');
                    form.action = nextUrl ? `/login?next=${encodeURIComponent(nextUrl)}` : '/login';
                    const csrfToken = getCsrfToken();
                    if (csrfToken) {
                        const csrfInput = document.createElement('input');
                        csrfInput.type = 'hidden';
                        csrfInput.name = 'csrf_token';
                        csrfInput.value = csrfToken;
                        form.appendChild(csrfInput);
                    }
                    const tokenInput = document.createElement('input');
                    tokenInput.type = 'hidden';
                    tokenInput.name = 'idToken';
                    tokenInput.value = idToken;
                    form.appendChild(tokenInput);
                    document.body.appendChild(form);
                    form.submit();
                } catch (error) {
                    console.error("Error during automatic backend login:", error);
                    sessionStorage.removeItem('autologin_attempted'); // Allow retry on error
                }
            }
            return; // Stop further execution
        }
        
        // If on a protected nutritionist page, run page-specific logic
        if (currentPath.includes('/pacientes_dashboard')) {
            loadAllPatients();
            updateWelcomeMessage();
        } else if (currentPath.includes('/profile')) {
            loadUserProfile();
        }
        
        // If we successfully navigated away from the login page, clear the flag.
        sessionStorage.removeItem('autologin_attempted');
    }
    // --- End of Logic for Nutritionist App ---

  } else { // User is NOT authenticated with Firebase
    console.log("AUTH_STATE: Usuario no logueado en Firebase.");
    localStorage.removeItem('authToken');
    localStorage.removeItem('patientAuthToken');
    console.log("AUTH_STATE: Firebase ID Token removed from localStorage.");

    // --- Logic for Nutritionist App ---
    if (!isPatientRoute) {
        if (loginLink) loginLink.style.display = 'block';
        if (registerLink) registerLink.style.display = 'block';
        if (profileLink) profileLink.style.display = 'none';
        if (logoutLink) logoutLink.style.display = 'none';
    }
    // --- End of Logic for Nutritionist App ---

    // --- Logic for Patient App ---
    if (isPatientRoute) {
        console.log('User is not signed in to patient app. Redirecting to patient login.');
        const loginUrl = `/patient/login?next=${encodeURIComponent(currentPath)}`;
        if (currentPath !== '/patient/login') window.location.href = loginUrl;
    }
    // --- End of Logic for Patient App ---
  }
}

// --- Helper Functions ---
function getNumber(id) {
  const element = document.getElementById(id);
  if (!element || element.value === "" || element.value === null) return null;
  const num = parseFloat(element.value);
  return isNaN(num) ? null : num;
}

function getList(id) {
  const element = document.getElementById(id);
  if (element && typeof element.value === 'string' && element.value.trim() !== '') {
    try {
      const tags = JSON.parse(element.value);
      if (Array.isArray(tags)) return tags.map(tag => tag.value.trim()).filter(Boolean);
    } catch (e) { /* Not JSON, treat as CSV */ }
    return element.value.split(',').map(s => s.trim()).filter(Boolean);
  }
  return [];
}

function showFinalMessage(message, type = "info", autoHide = true) {
  const finalMessageDiv = document.getElementById("final-message");
  if (!finalMessageDiv) return;
  finalMessageDiv.innerHTML = message;
  finalMessageDiv.className = `mt-3 alert alert-${type} alert-dismissible fade show`;
  finalMessageDiv.style.display = "block";
  if (autoHide) {
    setTimeout(() => {
      try {
        const alertInstance = bootstrap.Alert.getOrCreateInstance(finalMessageDiv);
        if (alertInstance) alertInstance.close();
      } catch (e) { finalMessageDiv.style.display = "none"; }
    }, 7000);
  }
}

function hideFinalMessage() {
  const finalMessageDiv = document.getElementById("final-message");
  if (finalMessageDiv) finalMessageDiv.style.display = "none";
}

async function sendPlanByEmail(evaluationId) {
  const emailBtn = document.getElementById('sendEmailToPatientButton');
  const originalHtml = emailBtn ? emailBtn.innerHTML : '';
  if (emailBtn) {
    emailBtn.disabled = true;
    emailBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Enviando...';
  }
  hideFinalMessage();
  try {
    const user = auth.currentUser;
    if (!user) throw new Error('No autenticado. Por favor, inicie sesión.');
    const token = await user.getIdToken();

    const resp = await fetch(`/enviar_plan_por_email/${evaluationId}`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'X-CSRFToken': getCsrfToken()
      }
    });
    const result = await resp.json();
    if (!resp.ok) throw new Error(result.error || `Error del servidor: ${resp.status}`);
    showFinalMessage(result.message || 'Email enviado correctamente.', 'success');
  } catch (err) {
    console.error('Error en sendPlanByEmail:', err);
    showFinalMessage(`Error al enviar email: ${err.message}`, 'danger');
  } finally {
    if (emailBtn) {
      emailBtn.disabled = false;
      emailBtn.innerHTML = originalHtml || '<i class="fas fa-envelope"></i> Enviar Plan';
    }
  }
}

function getCsrfToken() {
  const csrfTokenElement = document.querySelector('meta[name="csrf-token"]');
  if (csrfTokenElement) {
    return csrfTokenElement.getAttribute('content');
  }
  const hiddenInput = document.querySelector('input[name="csrf_token"]');
  if (hiddenInput) return hiddenInput.value;
  console.error("CSRF token not found in meta tag or hidden input.");
  return null;
}

function setupActionButtons(evaluationId) {
  const techPdfButton = document.getElementById("viewTechnicalPdfButton");
  const patientPdfButton = document.getElementById("viewPatientPdfButton");
  const sendEmailButton = document.getElementById("sendEmailToPatientButton");

  const setupButton = (button, url) => {
    if (button) {
      if (url) {
        button.onclick = () => window.open(url, "_blank");
        button.style.display = "inline-block";
      } else {
        button.style.display = "none";
      }
    }
  };

  setupButton(techPdfButton, evaluationId ? `/ver_pdf/${evaluationId}` : null);
  setupButton(patientPdfButton, evaluationId ? `/ver_pdf_paciente/${evaluationId}` : null);
  if (sendEmailButton) {
      if (evaluationId) {
        sendEmailButton.onclick = () => sendPlanByEmail(evaluationId);
        sendEmailButton.style.display = "inline-block";
      } else {
        sendEmailButton.style.display = "none";
      }
  }
}

// --- Form Population Logic ---
function populateTagifyField(fieldName, dataArray) {
  if (!Array.isArray(dataArray)) dataArray = [];
  const stringDataArray = dataArray.map(item => String(item || '').trim()).filter(Boolean);
  if (window.tagifyInstances && window.tagifyInstances[fieldName]) {
    const tagifyInstance = window.tagifyInstances[fieldName];
    tagifyInstance.removeAllTags();
    if (stringDataArray.length > 0) tagifyInstance.addTags(stringDataArray);
  } else {
    const inputElement = document.getElementById(fieldName);
    if (inputElement) inputElement.value = stringDataArray.join(',');
  }
}

function populatePreloadForm(data, actionContext) {
  console.log("Populating form. Context:", actionContext);
  if (!data || typeof data !== 'object') return;

  function setValue(id, value, isDate = false) {
    const el = document.getElementById(id);
    if (el) {
      let finalValue = value !== null && value !== undefined ? String(value) : '';
      if (isDate && finalValue && finalValue.includes('T')) finalValue = finalValue.split('T')[0];
      el.value = finalValue;
    }
  }

  setValue('patient_id', data.patient_id || data.id);
  setValue('name', data.name);
  setValue('surname', data.surname);
  setValue('cedula', data.cedula);
  setValue('email', data.email);
  setValue('phone_number', data.phone_number);
  setValue('education_level', data.education_level);
  setValue('purchasing_power', data.purchasing_power);
  setValue('dob', data.dob, true);
  setValue('sex', data.sex);
  setValue('height_cm', data.height_cm);

  const tagifyFields = ['allergies', 'intolerances', 'preferences', 'aversions'];
  tagifyFields.forEach(fieldName => populateTagifyField(fieldName, data[fieldName] || []));

  if (actionContext === 'edit_evaluation' || actionContext === 'load_eval_for_new') {
    setValue('loaded_evaluation_id', data.evaluation_id);
    const loadedDisplay = document.getElementById('loaded_evaluation_id_display');
    if (loadedDisplay) {
      loadedDisplay.textContent = actionContext === 'edit_evaluation'
        ? `Editando Evaluación ID: ${data.evaluation_id}.`
        : `Cargando datos desde Evaluación ID: ${data.evaluation_id} como base.`;
    }
    setValue('weight_at_plan', data.weight_at_eval || data.weight_at_plan);
    setValue('wrist_circumference_cm', data.wrist_circumference_cm);
    setValue('waist_circumference_cm', data.waist_circumference_cm);
    setValue('hip_circumference_cm', data.hip_circumference_cm);
    setValue('gestational_age_weeks', data.gestational_age_weeks !== null ? data.gestational_age_weeks : '0');
    setValue('activity_factor', data.activity_factor || '1.2');
    
    const pathologiesFromServer = data.pathologies || [];
    document.querySelectorAll('input[name="pathologies"]').forEach(checkbox => {
      checkbox.checked = pathologiesFromServer.includes(checkbox.value);
    });
    setValue('other_pathologies_text', data.other_pathologies_text);
    setValue('postoperative_text', data.postoperative_text);
    setValue('diet_type', data.diet_type);
    setValue('other_diet_type_text', data.other_diet_type_text);
    setValue('target_weight', data.target_weight);
    setValue('target_waist_cm', data.target_waist_cm);
    setValue('target_protein_perc', data.target_protein_perc);
    setValue('target_carb_perc', data.target_carb_perc);
    setValue('target_fat_perc', data.target_fat_perc);
    
    const micronutrients = data.micronutrients || {};
    setValue('mic_k', micronutrients.potassium_mg);
    setValue('mic_ca', micronutrients.calcium_mg);
    setValue('mic_na', micronutrients.sodium_mg);
    setValue('mic_chol', micronutrients.cholesterol_mg);

    const planTextArea = document.getElementById('planTextArea');
    if (planTextArea) {
      planTextArea.value = data.edited_plan_text || "";
      if (data.edited_plan_text) document.getElementById('plan-display-section').style.display = 'block';
    }
    setValue('user_observations', data.user_observations);

    const baseFoodsContainer = document.getElementById('base-foods-container');
    if (baseFoodsContainer) {
        baseFoodsContainer.innerHTML = '';
        if (data.base_foods && Array.isArray(data.base_foods) && data.base_foods.length > 0) {
            data.base_foods.forEach(food => addBaseFoodRow(food));
        }
    }
  }
  setTimeout(calcularValores, 150);
}

async function loadUserProfile() {
    const profileEmailInput = document.getElementById('profile-email');
    const profileNameInput = document.getElementById('profile-name');
    const profileSurnameInput = document.getElementById('profile-surname');
    const profileProfessionSelect = document.getElementById('profile-profession');
    const profileLicenseNumberInput = document.getElementById('profile-license-number');
    const profileCityInput = document.getElementById('profile-city');
    const profileCountrySelect = document.getElementById('profile-country');
    const profileAddressInput = document.getElementById('profile-address');
    const profilePhoneCodeSelect = document.getElementById('profile-phone-code');
    const profileLocalPhoneNumberInput = document.getElementById('profile-local-phone-number');
    const profileMessageDiv = document.getElementById('profile-message');

    if (!profileEmailInput || !profileNameInput) return;

    try {
        const user = auth.currentUser;
        if (!user) throw new Error("No autenticado. Por favor, inicia sesión de nuevo.");
        const token = await user.getIdToken();

        const response = await fetch('/api/user_info', {
            method: 'GET',
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ error: `Error del servidor: ${response.status}` }));
            throw new Error(errorData.error);
        }
        const userInfo = await response.json();
        profileEmailInput.value = userInfo.email || '';
        profileNameInput.value = userInfo.name || '';
        profileSurnameInput.value = userInfo.surname || '';
        profileProfessionSelect.value = userInfo.profession || '';
        profileLicenseNumberInput.value = userInfo.license_number || '';
        profileCityInput.value = userInfo.city || '';
        profileCountrySelect.value = userInfo.country || '';
        profileAddressInput.value = userInfo.address || '';

        if (userInfo.phone_number) {
            const phoneMatch = userInfo.phone_number.match(/^(\+\d+)\s*(.*)$/);
            if (phoneMatch) {
                profilePhoneCodeSelect.value = phoneMatch[1];
                profileLocalPhoneNumberInput.value = phoneMatch[2];
            } else {
                profileLocalPhoneNumberInput.value = userInfo.phone_number;
            }
        } else {
            profilePhoneCodeSelect.value = '';
            profileLocalPhoneNumberInput.value = '';
        }
    } catch (error) {
        console.error("Error loading user profile:", error);
        profileMessageDiv.textContent = `Error al cargar el perfil: ${error.message}`;
        profileMessageDiv.className = 'alert alert-danger mt-3';
        profileMessageDiv.style.display = 'block';
    }
}

async function handleProfileUpdate(event) {
    event.preventDefault();
    const profileNameInput = document.getElementById('profile-name');
    const profileMessageDiv = document.getElementById('profile-message');
    const spinner = document.getElementById('profile-spinner');

    profileMessageDiv.style.display = 'none';
    profileMessageDiv.classList.remove('alert-success', 'alert-danger');
    spinner.style.display = 'inline-block';

    try {
        const user = auth.currentUser;
        if (!user) throw new Error("No autenticado. Por favor, inicia sesión de nuevo.");
        const token = await user.getIdToken();

        const profilePhoneCodeSelect = document.getElementById('profile-phone-code');
        const profileLocalPhoneNumberInput = document.getElementById('profile-local-phone-number');
        const fullPhoneNumber = (profilePhoneCodeSelect.value ? profilePhoneCodeSelect.value + ' ' : '') + profileLocalPhoneNumberInput.value.trim();

        const updatedProfileData = {
            name: profileNameInput.value,
            surname: document.getElementById('profile-surname').value,
            profession: document.getElementById('profile-profession').value,
            license_number: document.getElementById('profile-license-number').value,
            city: document.getElementById('profile-city').value,
            country: document.getElementById('profile-country').value,
            address: document.getElementById('profile-address').value,
            phone_number: fullPhoneNumber
        };

        const response = await fetch('/api/user/profile', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}`, 'X-CSRFToken': getCsrfToken() },
            body: JSON.stringify(updatedProfileData)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `Error del servidor: ${response.status}`);
        profileMessageDiv.textContent = result.message;
        profileMessageDiv.className = 'alert alert-success mt-3';
        profileMessageDiv.style.display = 'block';

    } catch (error) {
        console.error("Error updating user profile:", error);
        profileMessageDiv.textContent = `Error al actualizar el perfil: ${error.message}`;
        profileMessageDiv.className = 'alert alert-danger mt-3';
        profileMessageDiv.style.display = 'block';
    } finally {
        spinner.style.display = 'none';
    }
}

async function updateWelcomeMessage() {
    const welcomeMessageElement = document.getElementById('welcome-message');
    if (!welcomeMessageElement) return;

    try {
        const user = auth.currentUser;
        if (!user) {
            welcomeMessageElement.textContent = 'Bienvenido/a.';
            return;
        }
        const token = await user.getIdToken();

        const response = await fetch('/api/user_info', {
            method: 'GET',
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.status === 401) {
            console.warn("Failed to fetch user info: Session expired.");
            welcomeMessageElement.textContent = 'Bienvenido/a.';
            return;
        }
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ error: `Error del servidor: ${response.status}` }));
            throw new Error(errorData.error);
        }

        const userInfo = await response.json();
        if (userInfo.name) {
            welcomeMessageElement.innerHTML = `Bienvenido/a, <strong>${userInfo.name}</strong>. Aquí puedes gestionar a tus pacientes.`;
        } else {
            welcomeMessageElement.textContent = 'Bienvenido/a.';
        }

    } catch (error) {
        console.error("Error updating welcome message:", error);
        welcomeMessageElement.textContent = 'Bienvenido/a.';
    }
}

// --- Dashboard Functions ---
function displayPatients(patientsList) {
    const resultsContainer = document.getElementById('patientSearchResultsDashboard');
    if (!resultsContainer) return;

    resultsContainer.innerHTML = '';
    if (patientsList && patientsList.length > 0) {
        patientsList.forEach(patient => {
            resultsContainer.innerHTML += `
                <div class="patient-card">
                    <h5>${patient.name} ${patient.surname}</h5>
                    <p>C.I.: ${patient.cedula}</p>
                    <a href="/paciente/${patient.id}/historial" class="btn btn-sm btn-outline-primary mt-2">
                        <i class="fas fa-history"></i> Ver Historial
                    </a>
                </div>`;
        });
    } else {
        resultsContainer.innerHTML = '<p class="text-center text-danger">No se encontraron pacientes.</p>';
    }
}

async function loadAllPatients() {
    const resultsContainer = document.getElementById('patientSearchResultsDashboard');
    if (!resultsContainer) return;
    resultsContainer.innerHTML = '<p class="text-center">Cargando todos los pacientes...</p>';

    try {
        const user = auth.currentUser;
        if (!user) throw new Error("No autenticado. Por favor, inicie sesión de nuevo.");
        const token = await user.getIdToken();

        const response = await fetch("/get_all_patients", {
            method: 'GET',
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.status === 401) throw new Error('Sesión expirada. Por favor, inicie sesión de nuevo.');
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ error: `Error del servidor: ${response.status}` }));
            throw new Error(errorData.error);
        }
        
        const data = await response.json();
        displayPatients(data.results || []);

    } catch (error) {
        console.error('Error al cargar todos los pacientes:', error);
        resultsContainer.innerHTML = `<p class="text-center text-danger">${error.message}</p>`;
    }
}

async function searchPatients(query) {
    const resultsContainer = document.getElementById('patientSearchResultsDashboard');
    if (!resultsContainer) return;
    resultsContainer.innerHTML = '<p class="text-center">Buscando...</p>';

    try {
        const user = auth.currentUser;
        if (!query.trim()) {
            loadAllPatients();
            return;
        }

        if (!user) throw new Error("No autenticado. Por favor, inicie sesión de nuevo.");
        const token = await user.getIdToken();

        const response = await fetch("/buscar_paciente", {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Authorization': `Bearer ${token}`
            },
            body: new URLSearchParams({ search_query: query })
        });

        if (response.status === 401) throw new Error('Sesión expirada. Por favor, inicie sesión de nuevo.');
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ error: `Error del servidor: ${response.status}` }));
            throw new Error(errorData.error);
        }
        
        const data = await response.json();
        const patientsToDisplay = data.results ? data.results.map(item => item.patient) : [];
        displayPatients(patientsToDisplay);

    } catch (error) {
        console.error('Error en búsqueda:', error);
        resultsContainer.innerHTML = `<p class="text-center text-danger">${error.message}</p>`;
    }
}

function initializeDashboard() {
    const searchForm = document.getElementById('searchPatientFormDashboard');
    if (searchForm) {
        searchForm.addEventListener('submit', function(event) {
            event.preventDefault();
            const query = document.getElementById('searchQueryDashboard').value;
            searchPatients(query);
        });
    }
}

function updateReferenceDisplay(containerId, rangesObject) {
    const container = document.getElementById(containerId);
    if (!container) return;

    let html = '';
    if (rangesObject && typeof rangesObject === 'object') {
        for (const [label, value] of Object.entries(rangesObject)) {
            if (label.startsWith('_')) continue; 
            html += `<small class="d-block"><strong>${label}:</strong> ${value}</small>`;
        }
    }
    container.innerHTML = html;
}

// --- Core Logic Functions ---
async function calcularValores() {
  console.log("****** calcularValores() triggered ******");
  const idsToFetch = {
    height: "height_cm", weight: "weight_at_plan", wrist: "wrist_circumference_cm",
    waist: "waist_circumference_cm", hip: "hip_circumference_cm", dob: "dob",
    sex: "sex", activity_factor: "activity_factor"
  };
  const data = {};
  for (const k in idsToFetch) {
    const element = document.getElementById(idsToFetch[k]);
    data[k] = element ? element.value : null;
  }
  if (!data.height || !data.weight || !data.dob || !data.sex || !data.activity_factor) {
    console.warn("Faltan datos básicos para calcularValores.");
    updateReferenceDisplay('imc-ranges', {});
    updateReferenceDisplay('whr-ranges', {});
    updateReferenceDisplay('whtr-ranges', {});
    return;
  }
  try {
    const resp = await fetch("/calcular_valores", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken()
      },
      body: JSON.stringify(data)
    });
    if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ error: `Error del servidor: ${resp.status}` }));
        throw new Error(errorData.error);
    }
    const res = await resp.json();
    
    const updateEl = (id, value, suffix = '', risk = null) => {
        const el = document.getElementById(id);
        if (el) {
            el.textContent = (value !== null && value !== undefined) ? `${value}${suffix}` : '--';
            if (risk) el.className = `value-display risk-${risk}`;
        }
    };
    updateEl("calculated-imc", res.imc?.toFixed(1), '', res.imc_risk);
    updateEl("calculated-complexion", res.complexion);
    updateEl("calculated-ideal-weight", res.ideal_weight?.toFixed(1), ' kg');
    updateEl("calculated-get", res.get?.toFixed(0), ' kcal');
    updateEl("calculated-whr", res.waist_hip_ratio?.toFixed(2), '', res.whr_risk);
    updateEl("calculated-whtr", res.waist_height_ratio?.toFixed(2), '', res.whtr_risk);
    lastCalculatedReferences = res.references || {};

    updateReferenceDisplay('imc-ranges', res.references?.imc);
    updateReferenceDisplay('whr-ranges', res.references?.whr);
    updateReferenceDisplay('whtr-ranges', res.references?.whtr);
  } catch (err) {
    console.error("ERROR en calcularValores:", err);
    showFinalMessage(`Error en cálculo: ${err.message}`, "danger");
  }
}

async function loadRelevantPreparationsForPatient() {
  const container = document.getElementById('relevant-preparations-container');
  if (!container) return;
  container.innerHTML = '<p id="relevant-preparations-placeholder">Buscando...</p>';
  const patientData = {
    pathologies: Array.from(document.querySelectorAll('input[name="pathologies"]:checked')).map(cb => cb.value),
    aversions: getList('aversions'),
    allergies: getList('allergies'),
    intolerances: getList('intolerances'),
    diet_type: document.getElementById('diet_type')?.value || '',
    other_diet_type_text: document.getElementById('other_diet_type_text')?.value || ''
  };
  try {
    const user = auth.currentUser;
    if (!user) throw new Error("No autenticado. Por favor, inicie sesión.");
    const token = await user.getIdToken();

    const response = await fetch('/api/relevant_preparations_for_patient', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
        "X-CSRFToken": getCsrfToken()
      },
      body: JSON.stringify(patientData)
    });
    if (response.status === 401) throw new Error('Sesión expirada.');
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ error: `Error del servidor: ${response.status}` }));
      throw new Error(errorData.error);
    }
    const preparations = await response.json();
    container.innerHTML = '';
    if (preparations.length === 0) {
      container.innerHTML = '<p class="text-muted">No se encontraron preparaciones relevantes.</p>';
      return;
    }
    preparations.forEach(prep => {
      const div = document.createElement('div');
      div.classList.add('form-check', 'mb-2');
      div.innerHTML = `<input class="form-check-input" type="checkbox" value="${prep.id}" id="prep_fav_${prep.id}" data-preparation='${JSON.stringify(prep)}'><label class="form-check-label" for="prep_fav_${prep.id}"><strong>${prep.name}</strong></label>`;
      container.appendChild(div);
    });
  } catch (error) {
    console.error("Error en loadRelevantPreparationsForPatient:", error);
    container.innerHTML = `<p class="text-danger">Error al cargar: ${error.message}</p>`;
  }
}

async function generarPlan() {
  console.log("****** generarPlan() triggered ******");
  hideFinalMessage();
  currentPlanDataBaseData = null;

  const micronutrients = {
    potassium_mg: getNumber('mic_k'),
    calcium_mg: getNumber('mic_ca'),
    sodium_mg: getNumber('mic_na'),
    cholesterol_mg: getNumber('mic_chol'),
  };

  let baseFoodsForPayload = [];
  const selectedRelevantPreps = window.selectedRelevantPreparations || [];
  if (selectedRelevantPreps.length > 0) {
    selectedRelevantPreps.forEach(prep => {
      if (prep && typeof prep === 'object' && prep.name && Array.isArray(prep.ingredients)) {
        baseFoodsForPayload.push({
          name: prep.name,
          original_ingredients: prep.ingredients.map(ing => ({ item: ing.item }))
        });
      }
    });
  }

  const baseFoodsFromDynamicSelects = getBaseFoodsFromDynamicSelects();
  baseFoodsFromDynamicSelects.forEach(foodName => {
    if (!baseFoodsForPayload.some(bf => typeof bf === 'object' && bf.name === foodName)) {
      baseFoodsForPayload.push(foodName);
    }
  });

  const pathologies = Array.from(document.querySelectorAll('input[name="pathologies"]:checked')).map(cb => cb.value);

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
    wrist_circumference_cm: parseFloat(document.getElementById("wrist_circumference_cm").value) || null,
    waist_circumference_cm: parseFloat(document.getElementById("waist_circumference_cm").value) || null,
    hip_circumference_cm: parseFloat(document.getElementById("hip_circumference_cm").value) || null,
    gestational_age_weeks: parseInt(document.getElementById("gestational_age_weeks").value, 10) || 0,
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
    target_protein_perc: parseFloat(document.getElementById("target_protein_perc").value) || null,
    target_carb_perc: parseFloat(document.getElementById("target_carb_perc").value) || null,
    target_fat_perc: parseFloat(document.getElementById("target_fat_perc").value) || null,
    micronutrients,
    base_foods: baseFoodsForPayload,
    references: lastCalculatedReferences
  };

  const required = [
    "name", "surname", "cedula", "dob", "sex", "height_cm", "weight_at_plan", "activity_factor", "target_weight"
  ];
  const missing = required.filter((f) => !planBaseData[f]);
  if (missing.length) {
    alert(`Complete los campos: ${missing.join(", ")}`);
    return;
  }

  const sum = (planBaseData.target_protein_perc || 0) + (planBaseData.target_carb_perc || 0) + (planBaseData.target_fat_perc || 0);
  if (sum > 0 && (sum < 98 || sum > 102)) {
    alert("La suma de macros debe ser ≈100% o todos en 0.");
    return;
  }

  document.getElementById("plan-display-section").style.display = "block";
  const textarea = document.getElementById("planTextArea");
  textarea.value = "Generando plan con IA... Por favor, espere.";
  textarea.disabled = true;

  try {
    const user = auth.currentUser;
    if (!user) throw new Error("No autenticado. Por favor, inicie sesión.");
    const token = await user.getIdToken();

    const resp = await fetch("/generar_plan", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        'Authorization': `Bearer ${token}`,
        "X-CSRFToken": getCsrfToken()
      },
      body: JSON.stringify(planBaseData)
    });
    if (resp.status === 401) throw new Error('Sesión expirada. Por favor, inicie sesión de nuevo.');
    const res = await resp.json();
    if (!resp.ok) throw new Error(res.error || `Error del servidor: ${resp.status}`);
    
    textarea.value = res.gemini_raw_text || "Error: plan inválido recibido del servidor.";
    currentPlanDataBaseData = res.plan_data_for_save;

    const fullPlanText = res.gemini_raw_text;
    const recipeSectionMarker = "== RECETARIO DETALLADO ==";
    const parts = fullPlanText.split(recipeSectionMarker);
    const recipesDetailedText = parts.length > 1 ? parts[1].trim() : "";

    const recipesContainer = document.getElementById('favoriteRecipesContainer');
    const favoriteSelectionContainer = document.getElementById('favorite-recipes-selection-container');

    if (recipesContainer && favoriteSelectionContainer) {
        recipesContainer.innerHTML = '';
        favoriteSelectionContainer.style.display = 'none';

        if (recipesDetailedText) {
            const recipeMatches = [];
            const recipeStartPattern = /\n?\s*\**\s*(Receta\s*(?:N°|No\.|N\.)?\s*\d+:\s*[\s\S]+?)(?=\n\s*\**\s*Receta\s*(?:N°|No\.|N\.)?\s*\d+:|$)/gi;
            let match;

            while ((match = recipeStartPattern.exec(recipesDetailedText)) !== null) {
                if (match[1]) recipeMatches.push(match[1].trim());
            }

            if (recipeMatches.length > 0) {
                favoriteSelectionContainer.style.display = 'block';
                recipeMatches.forEach((fullRecipeBlock, index) => {
                    let recipeTitle = null;
                    const titleMatchAttempt1 = fullRecipeBlock.match(/^(Receta\s*(?:N°|No\.|N\.)?\s*\d+:\s*[^\n]+?)(?=\s*\n\s*(?:Rinde:|Porciones que Rinde:)|$)/i);
                    if (titleMatchAttempt1 && titleMatchAttempt1[1]) {
                        recipeTitle = titleMatchAttempt1[1].trim();
                    } else {
                        const titleMatchAttempt2 = fullRecipeBlock.match(/^(Receta\s*(?:N°|No\.|N\.)?\s*\d+:\s*[^\n]+)/i);
                        if (titleMatchAttempt2 && titleMatchAttempt2[0]) recipeTitle = titleMatchAttempt2[0].trim();
                    }
                    if (recipeTitle) {
                        const checkboxId = `fav_recipe_js_${index}`;
                        const div = document.createElement('div');
                        div.className = 'form-check mb-2';
                        div.innerHTML = `<input class="form-check-input" type="checkbox" value="${recipeTitle}" id="${checkboxId}" name="favorite_recipes_frontend"><label class="form-check-label" for="${checkboxId}">${recipeTitle}</label>`;
                        recipesContainer.appendChild(div);
                    }
                });
            } else {
                recipesContainer.innerHTML = '<p class="text-muted">No se encontraron recetas detalladas en el formato esperado para seleccionar.</p>';
                favoriteSelectionContainer.style.display = 'none';
            }
        } else {
            recipesContainer.innerHTML = '<p class="text-muted">La sección de recetas detalladas está vacía o no se pudo parsear.</p>';
            favoriteSelectionContainer.style.display = 'none';
        }
    }
  } catch (err) {
    if (textarea) textarea.value = `Error al generar el plan: ${err.message}`;
    showFinalMessage(`Error al generar el plan: ${err.message}`, "danger");
  } finally {
    if (textarea) textarea.disabled = false;
  }
}

async function finalizar() {
    console.log("****** INICIO finalizar() ******");
    hideFinalMessage();
    const planTextArea = document.getElementById("planTextArea");
    const editedPlanText = planTextArea ? planTextArea.value : null;
    if (!editedPlanText || editedPlanText.trim() === "") {
        showFinalMessage("Error: El plan generado no puede estar vacío para guardar.", "danger");
        return;
    }
    const form = document.getElementById('patient-plan-form');
    const formData = new FormData(form);
    const planData = Object.fromEntries(formData.entries());
    planData.allergies = getList('allergies');
    planData.intolerances = getList('intolerances');
    planData.preferences = getList('preferences');
    planData.aversions = getList('aversions');
    planData.base_foods = getBaseFoodsFromDynamicSelects();
    planData.pathologies = Array.from(document.querySelectorAll('input[name="pathologies"]:checked')).map(cb => cb.value);
    planData.references = lastCalculatedReferences;
    const payload = {
        plan_data: planData,
        edited_plan_text: editedPlanText,
        user_observations: document.getElementById("user_observations")?.value || "",
        selected_favorite_recipes: Array.from(document.querySelectorAll('input[name="favorite_recipes_frontend"]:checked')).map(cb => cb.value)
    };
    const loadedEvalId = document.getElementById("loaded_evaluation_id")?.value || null;
    const endpoint = loadedEvalId ? `/actualizar_evaluacion/${loadedEvalId}` : "/guardar_evaluacion";
    const method = loadedEvalId ? "PUT" : "POST";
    const saveButton = document.getElementById('btn-finalizar');
    if (saveButton) {
        saveButton.disabled = true;
        saveButton.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Guardando...`;
    }
    try {
        const user = auth.currentUser;
        if (!user) throw new Error("No autenticado. Por favor, inicie sesión.");
        const token = await user.getIdToken();

        const response = await fetch(endpoint, {
            method: method,
            headers: {
                "Content-Type": "application/json",
                'Authorization': `Bearer ${token}`,
                "X-CSRFToken": getCsrfToken()
            },
            body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `Error del servidor: ${response.status}`);
        showFinalMessage(result.message, "success", false);
        if (result.evaluation_id) {
            document.getElementById("loaded_evaluation_id").value = result.evaluation_id;
            setupActionButtons(result.evaluation_id);
        }
    } catch (error) {
        console.error("ERROR en finalizar():", error);
        showFinalMessage(`Error al guardar: ${error.message}`, "danger");
    } finally {
        if (saveButton) {
            saveButton.disabled = false;
            saveButton.innerHTML = '<i class="fas fa-save"></i> Guardar Evaluación y Archivos';
        }
    }
}

async function handleLogin(event) {
  event.preventDefault();
  const email = document.getElementById('login-email').value;
  const password = document.getElementById('login-password').value;
  const errorMessageDiv = document.getElementById('login-error-message');
  const spinner = document.getElementById('login-spinner');
  errorMessageDiv.classList.add('d-none');
  if(spinner) spinner.classList.remove('d-none');

  // Clear the auto-login attempt flag on manual login to reset the flow
  sessionStorage.removeItem('autologin_attempted');

  try {
    const userCredential = await signInWithEmailAndPassword(auth, email, password);
    const user = userCredential.user;
    const idToken = await user.getIdToken();

    const form = document.createElement('form');
    form.method = 'POST';
    // FIX: Ensure the 'next' parameter is passed along for correct redirection after login
    const nextUrl = new URLSearchParams(window.location.search).get('next');
    form.action = nextUrl ? `/login?next=${encodeURIComponent(nextUrl)}` : '/login';

    const csrfToken = getCsrfToken();
    if (csrfToken) {
        const csrfInput = document.createElement('input');
        csrfInput.type = 'hidden';
        csrfInput.name = 'csrf_token';
        csrfInput.value = csrfToken;
        form.appendChild(csrfInput);
    }

    const tokenInput = document.createElement('input');
    tokenInput.type = 'hidden';
    tokenInput.name = 'idToken';
    tokenInput.value = idToken;
    form.appendChild(tokenInput);

    document.body.appendChild(form);
    form.submit();

  } catch (error) {
    let friendlyMessage = "Correo electrónico o contraseña incorrectos.";
    if (error.code === 'auth/invalid-email') friendlyMessage = "El formato del correo es inválido.";
    errorMessageDiv.textContent = friendlyMessage;
    errorMessageDiv.classList.remove('d-none');
    console.error("Firebase Login Error:", error);
  } finally {
    if(spinner) spinner.classList.add('d-none');
  }
}

async function handleRegister(event) {
  event.preventDefault();
  const email = document.getElementById('register-email').value;
  const password = document.getElementById('register-password').value;
  const name = document.getElementById('register-name').value;
  const errorMessageDiv = document.getElementById('register-error-message');
  const spinner = document.getElementById('register-spinner');

  if(errorMessageDiv) errorMessageDiv.classList.add('d-none');
  if (spinner) spinner.style.display = 'inline-block';

  try {
    const userCredential = await createUserWithEmailAndPassword(auth, email, password);
    const user = userCredential.user;

    if (name) {
      await updateProfile(user, { displayName: name });
    }

    const idToken = await user.getIdToken();

    const form = document.createElement('form');
    form.method = 'POST';
    form.action = '/login';

    const csrfToken = getCsrfToken();
    if (csrfToken) {
        const csrfInput = document.createElement('input');
        csrfInput.type = 'hidden';
        csrfInput.name = 'csrf_token';
        csrfInput.value = csrfToken;
        form.appendChild(csrfInput);
    }

    const tokenInput = document.createElement('input');
    tokenInput.type = 'hidden';
    tokenInput.name = 'idToken';
    tokenInput.value = idToken;
    form.appendChild(tokenInput);

    document.body.appendChild(form);
    form.submit();

  } catch (error) {
    console.error("Firebase Registration Error:", error);
    let friendlyMessage = "Error al crear la cuenta. Inténtalo de nuevo.";
    if (error.code === 'auth/email-already-in-use') friendlyMessage = "Este correo ya está registrado.";
    else if (error.code === 'auth/weak-password') friendlyMessage = "La contraseña debe tener al menos 6 caracteres.";
    else if (error.code === 'auth/invalid-email') friendlyMessage = "El formato del correo electrónico es inválido.";
    if(errorMessageDiv) {
        errorMessageDiv.textContent = friendlyMessage;
        errorMessageDiv.classList.remove('d-none');
        errorMessageDiv.style.display = 'block';
    }
  } finally {
    if (spinner) spinner.style.display = 'none';
  }
}

async function handleLogout(event) {
  event.preventDefault();
  clearPatientChatInterval(); // Detener el chat del paciente al cerrar sesión
  try {
    sessionStorage.removeItem('autologin_attempted'); // Clear the flag on logout
    await signOut(auth);
    window.location.href = '/logout';
  } catch (error) {
    sessionStorage.removeItem('autologin_attempted'); // Also clear on error
    console.error("Logout Error:", error);
    window.location.href = '/logout';
  }
}

function clearPatientChatInterval() {
    if (patientChatIntervalId) {
        clearInterval(patientChatIntervalId);
        patientChatIntervalId = null;
        console.log("TIMER_DEBUG: Intervalo de chat del paciente detenido.");
    }
}

// --- Patient Portal Functions ---

async function handlePatientLogin(event) {
    event.preventDefault();
    const email = document.getElementById('patient-login-email').value;
    const password = document.getElementById('patient-login-password').value;
    const errorMessageDiv = document.getElementById('patient-login-error-message');
    const spinner = document.getElementById('patient-login-spinner');
    
    if (errorMessageDiv) errorMessageDiv.classList.add('d-none');
    if (spinner) spinner.classList.remove('d-none');

    try {
        const userCredential = await signInWithEmailAndPassword(auth, email, password);
        const user = userCredential.user;
        const idToken = await user.getIdToken();
        
        // Store the token for the patient app to use
        localStorage.setItem('patientAuthToken', idToken);
        console.log("PATIENT_AUTH: Token de paciente guardado en localStorage.");

        // Redirect to the patient dashboard
        window.location.href = '/patient/dashboard';

    } catch (error) {
        let friendlyMessage = "Correo electrónico o contraseña incorrectos.";
        if (error.code === 'auth/invalid-email') {
            friendlyMessage = "El formato del correo es inválido.";
        } else if (error.code === 'auth/wrong-password' || error.code === 'auth/user-not-found' || error.code === 'auth/invalid-credential') {
            friendlyMessage = "Correo electrónico o contraseña incorrectos.";
        }
        if(errorMessageDiv) {
            errorMessageDiv.textContent = friendlyMessage;
            errorMessageDiv.classList.remove('d-none');
        }
        console.error("Firebase Patient Login Error:", error);
    } finally {
        if (spinner) spinner.classList.add('d-none');
    }
}

async function loadPatientDashboard() {
    const dashboardContainer = document.getElementById('patient-dashboard-content');
    if (!dashboardContainer) return;

    const token = localStorage.getItem('patientAuthToken');
    if (!token) {
        console.log("PATIENT_DASHBOARD: No hay token, redirigiendo al login de paciente.");
        window.location.href = '/patient/login';
        return;
    }

    try {
        const response = await fetch('/api/patient/me/latest_plan', {
            method: 'GET',
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.status === 401) {
            localStorage.removeItem('patientAuthToken');
            throw new Error("Tu sesión ha expirado. Por favor, ingresa de nuevo.");
        }
        
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || `Error del servidor: ${response.status}`);

        dashboardContainer.innerHTML = `<div class="card shadow-sm"><div class="card-header bg-primary text-white"><h3 class="mb-0">Mi Plan Nutricional</h3></div><div class="card-body"><h5 class="card-title">Hola, ${data.patient_name}</h5><p class="card-text text-muted">Este es tu plan correspondiente a la consulta del ${data.consultation_date}.</p><hr><div class="plan-text-container">${data.plan_text}</div><hr><h6 class="mt-4">Observaciones del Nutricionista:</h6><p class="text-muted fst-italic">${data.nutritionist_observations}</p></div></div>`;

        // Mostrar el botón para abrir el chat
        const showChatBtnContainer = document.getElementById('show-chat-button-container');
        if (showChatBtnContainer) showChatBtnContainer.style.display = 'block';

    } catch (error) {
        console.error("Error loading patient dashboard:", error);
        dashboardContainer.innerHTML = `<div class="alert alert-danger">Error al cargar tu plan: ${error.message}</div>`;
        if (error.message.includes("expirado")) setTimeout(() => { window.location.href = '/patient/login'; }, 3000);
    }
}

function addPatientMessageToChat(message, chatContainer) {
    if (!chatContainer || !message) return;
    if (document.getElementById(`patient-msg-${message.id}`)) return;

    const messageWrapper = document.createElement('div');
    messageWrapper.id = `patient-msg-${message.id}`;
    const messageElement = document.createElement('div');

    const isSentByPatient = message.sender_is_patient;
    messageWrapper.className = `d-flex flex-column ${isSentByPatient ? 'align-items-end' : 'align-items-start'} w-100`;
    messageElement.className = `patient-message ${isSentByPatient ? 'patient-message-sent' : 'patient-message-received'}`;
    messageElement.textContent = message.message_text;

    const timestampElement = document.createElement('span');
    timestampElement.className = 'patient-message-timestamp';
    timestampElement.textContent = new Date(message.timestamp).toLocaleString();
    messageElement.appendChild(timestampElement);

    messageWrapper.appendChild(messageElement);
    chatContainer.appendChild(messageWrapper);
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

async function loadPatientMessages(chatContainer) {
    const token = localStorage.getItem('patientAuthToken');
    if (!token) return;

    try {
        const response = await fetch('/api/patient/me/chat/messages', { headers: { 'Authorization': `Bearer ${token}` } });
        if (!response.ok) throw new Error('No se pudieron cargar los mensajes.');
        const data = await response.json();
        
        if (chatContainer.querySelector('p')) chatContainer.innerHTML = '';
        if (data.messages && data.messages.length > 0) {
            data.messages.forEach(msg => addPatientMessageToChat(msg, chatContainer));
        } else {
            chatContainer.innerHTML = '<p class="text-muted text-center">Aún no hay mensajes. ¡Envía el primero!</p>';
        }
    } catch (error) {
        console.error("Error cargando mensajes del paciente:", error);
    }
}

async function sendPatientMessage(messageInput, chatContainer) {
    const messageText = messageInput.value.trim();
    if (!messageText) return;

    const token = localStorage.getItem('patientAuthToken');
    if (!token) { alert("Tu sesión ha expirado. Por favor, inicia sesión de nuevo."); return; }

    try {
        const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
        const response = await fetch('/api/patient/me/chat/messages', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`,
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ content: messageText })
        });
        if (!response.ok) throw new Error('Error al enviar el mensaje.');
        const newMessage = await response.json();
        if (chatContainer.querySelector('p')) chatContainer.innerHTML = '';
        addPatientMessageToChat(newMessage, chatContainer);
        messageInput.value = '';
    } catch (error) {
        console.error("Error enviando mensaje del paciente:", error);
        alert(error.message);
    }
}

function initializePatientChat() {
    const chatContainer = document.getElementById('patient-chat-container');
    const messageInput = document.getElementById('patientChatMessageInput');
    const sendButton = document.getElementById('sendPatientChatMessageButton');

    if (!chatSection || !chatContainer || !messageInput || !sendButton) return;

    // Hacer visible la sección de chat al inicializar
    chatSection.style.display = 'block'; // Hacer visible la sección de chat

    sendButton.addEventListener('click', () => sendPatientMessage(messageInput, chatContainer));
    messageInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendPatientMessage(messageInput, chatContainer);
    });

    // Cargar mensajes inmediatamente
    loadPatientMessages(chatContainer);
    
    // Limpiar cualquier intervalo anterior antes de crear uno nuevo
    clearPatientChatInterval();

    // Empezar a revisar periódicamente y guardar el ID del intervalo
    patientChatIntervalId = setInterval(() => {
        console.log("TIMER_DEBUG: Polling de chat del paciente ejecutándose...");
        loadPatientMessages(chatContainer);
    }, 7000);
}

// --- Base Foods Dynamic Rows ---
function addBaseFoodRow(foodName = '') {
    const container = document.getElementById('base-foods-container');
    if (!container) {
        console.error("Container 'base-foods-container' not found.");
        return;
    }

    const row = document.createElement('div');
    row.classList.add('input-group', 'input-group-sm', 'mb-2', 'base-food-row');

    const select = document.createElement('select');
    select.classList.add('form-select', 'base-food-select');
    select.name = 'base_foods';

    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = 'Seleccionar alimento...';
    select.appendChild(defaultOption);

    if (window.allIngredientsData && Array.isArray(window.allIngredientsData)) {
        window.allIngredientsData.forEach(ingredient => {
            const option = document.createElement('option');
            option.value = ingredient.name;
            option.textContent = ingredient.name;
            if (ingredient.name === foodName) { option.selected = true; }
            select.appendChild(option);
        });
    }

    const removeButton = document.createElement('button');
    removeButton.classList.add('btn', 'btn-outline-danger');
    removeButton.type = 'button';
    removeButton.innerHTML = '<i class="fas fa-trash"></i>';
    removeButton.onclick = () => row.remove();

    row.appendChild(select);
    row.appendChild(removeButton);
    container.appendChild(row);
}

function getBaseFoodsFromDynamicSelects() {
    const baseFoods = [];
    const selectElements = document.querySelectorAll('#base-foods-container .base-food-select');
    selectElements.forEach(select => {
        if (select.value && select.value.trim() !== '') {
            baseFoods.push(select.value.trim());
        }
    });
    return baseFoods;
}

function initializeEventListeners() {
    // Nutricionista
    const loginForm = document.getElementById('login-form');
    if (loginForm) loginForm.addEventListener('submit', handleLogin);

    const registerForm = document.getElementById('register-form');
    if (registerForm) registerForm.addEventListener('submit', handleRegister);

    const profileForm = document.getElementById('profile-form');
    if (profileForm) profileForm.addEventListener('submit', handleProfileUpdate);

    const logoutLink = document.getElementById('logout-link');
    if (logoutLink) logoutLink.addEventListener('click', handleLogout);

    // Paciente
    const patientLoginForm = document.getElementById('patient-login-form');
    if (patientLoginForm) patientLoginForm.addEventListener('submit', handlePatientLogin);

    const showChatButton = document.getElementById('show-chat-button');
    if (showChatButton) {
        showChatButton.addEventListener('click', function() {
            initializePatientChat();
            this.style.display = 'none'; // Ocultar el botón después de hacer clic
        });
    }
    const inviteButton = document.getElementById('invite-patient-btn');
    if (inviteButton) {
        inviteButton.addEventListener('click', function() {
            const patientId = this.dataset.patientId;
            if (!confirm(`¿Estás seguro de que quieres enviar una invitación por email a este paciente para que cree su cuenta?`)) {
                return;
            }

            // Obtener el token CSRF del meta tag
            const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

            // showSpinner(); // Comentado temporalmente, ya que la función no está definida globalmente

            fetch(`/paciente/${patientId}/invitar`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken // Enviar el token CSRF
                },
            })
            .then(response => response.json())
            .then(data => {
                // hideSpinner(); // Comentado temporalmente
                if (data.error) {
                    alert('Error al invitar al paciente: ' + data.error);
                } else {
                    alert(data.message);
                    // Opcional: Recargar la página para que el botón cambie a "Invitado"
                    window.location.reload(); 
                }
            })
            .catch(error => {
                // hideSpinner(); // Comentado temporalmente
                console.error('Error en la solicitud de invitación:', error);
                alert('Error de red al intentar invitar al paciente.');
            });
        });
    }

    // Dashboard Nutricionista
    const dashboardContainer = document.getElementById('patientSearchResultsDashboard');
    if (dashboardContainer) initializeDashboard();

    // Formulario de Evaluación
    if (document.getElementById('patient-plan-form')) {
        initializeEvaluationForm();
    }

    // Perfil de Nutricionista
    const profileCountrySelect = document.getElementById('profile-country');
    const profilePhoneCodeSelect = document.getElementById('profile-phone-code');
    if (profileCountrySelect && profilePhoneCodeSelect) {
        profileCountrySelect.addEventListener('change', () => {
            const selectedOption = profileCountrySelect.options[profileCountrySelect.selectedIndex];
            const phoneCode = selectedOption.dataset.phoneCode;
            profilePhoneCodeSelect.value = phoneCode || '';
        });
    }
}

function initializeEvaluationForm() {
    console.log("Formulario de Evaluación: DOMContentLoaded disparado.");
    const tagifyElements = [
        { selector: 'input[name="allergies"]', id: 'allergies' },
        { selector: 'input[name="intolerances"]', id: 'intolerances' },
        { selector: 'input[name="preferences"]', id: 'preferences' },
        { selector: 'input[name="aversions"]', id: 'aversions' }
    ];
    window.tagifyInstances = {};
    tagifyElements.forEach(item => {
        const input = document.querySelector(item.selector);
        if (input) {
            try { window.tagifyInstances[item.id] = new Tagify(input); }
            catch (e) { console.error(`Falló Tagify para ${item.id}:`, e); }
        }
    });

    const serverContextAction = window.serverContextAction;
    const serverContextEvaluationData = window.serverContextEvaluationData;

    if ((serverContextAction === 'edit_evaluation' || serverContextAction === 'load_eval_for_new') && serverContextEvaluationData) {
        console.log("Formulario de Evaluación: Modo EDICIÓN con datos del servidor.");
        populatePreloadForm(serverContextEvaluationData, 'edit_evaluation');
        const saveButton = document.getElementById('btn-finalizar');
        if (saveButton) saveButton.innerHTML = '<i class="fas fa-save"></i> Guardar Cambios';
    } else {
        console.log("Formulario de Evaluación: Modo NUEVO.");
        if (document.getElementById('base-foods-container')) addBaseFoodRow();
    }

    const watch = ["height_cm", "weight_at_plan", "wrist_circumference_cm", "waist_circumference_cm", "hip_circumference_cm", "dob", "sex", "activity_factor"];
    watch.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("change", calcularValores);
    });

    document.getElementById("btn-load-relevant-preparations")?.addEventListener("click", loadRelevantPreparationsForPatient);
    document.getElementById("btn-generar-plan")?.addEventListener("click", generarPlan);
    document.getElementById('btn-finalizar')?.addEventListener('click', finalizar);
    document.getElementById('btn-add-base-food')?.addEventListener('click', () => addBaseFoodRow());
}

// --- DOMContentLoaded Listener ---
document.addEventListener("DOMContentLoaded", () => {
    onAuthStateChanged(auth, handleAuthStateChange);
    initializeEventListeners();
    if (window.location.pathname.includes('/patient/dashboard')) {
        loadPatientDashboard();
    } else {
        // Si no estamos en el dashboard del paciente, nos aseguramos de que el timer esté detenido.
        clearPatientChatInterval();
    }
});

console.log("DEBUG_UI: main.js loaded.");
