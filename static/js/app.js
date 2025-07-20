// Global JavaScript functions for Autolavaggio Management System

// CSRF Token helper
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

const csrftoken = getCookie('csrftoken');

// Configure fetch to include CSRF token
function fetchWithCSRF(url, options = {}) {
    const defaultOptions = {
        headers: {
            'X-CSRFToken': csrftoken,
            'Content-Type': 'application/json',
        },
        credentials: 'same-origin'
    };
    
    return fetch(url, { ...defaultOptions, ...options });
}

// Show loading spinner
function showLoading(element) {
    if (element) {
        element.innerHTML = '<span class="spinner"></span> Caricamento...';
        element.disabled = true;
    }
}

// Hide loading spinner
function hideLoading(element, originalText) {
    if (element) {
        element.innerHTML = originalText;
        element.disabled = false;
    }
}

// Show toast notification
function showToast(message, type = 'info') {
    const toastContainer = document.getElementById('toast-container') || createToastContainer();
    
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${type} border-0`;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');
    
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                ${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>
    `;
    
    toastContainer.appendChild(toast);
    
    const bsToast = new bootstrap.Toast(toast);
    bsToast.show();
    
    // Remove toast after it's hidden
    toast.addEventListener('hidden.bs.toast', () => {
        toast.remove();
    });
}

function createToastContainer() {
    const container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
    container.style.zIndex = '1055';
    document.body.appendChild(container);
    return container;
}

// Format currency
function formatCurrency(amount) {
    return new Intl.NumberFormat('it-IT', {
        style: 'currency',
        currency: 'EUR'
    }).format(amount);
}

// Format date
function formatDate(dateString) {
    return new Intl.DateTimeFormat('it-IT').format(new Date(dateString));
}

// Format datetime
function formatDateTime(dateString) {
    return new Intl.DateTimeFormat('it-IT', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    }).format(new Date(dateString));
}

// Auto refresh page every X minutes (for dashboards)
function setupAutoRefresh(minutes = 5) {
    setInterval(() => {
        window.location.reload();
    }, minutes * 60 * 1000);
}

// Confirm dialog helper
function confirmAction(message, callback) {
    if (confirm(message)) {
        callback();
    }
}

// Toggle fields based on service/product type
function toggleFields() {
    const tipoField = document.querySelector('select[name="tipo"]');
    const servizioFields = document.getElementById('servizio-fields');
    const prodottoFields = document.getElementById('prodotto-fields');
    
    if (tipoField && servizioFields && prodottoFields) {
        if (tipoField.value === 'servizio') {
            servizioFields.style.display = 'block';
            prodottoFields.style.display = 'none';
        } else {
            servizioFields.style.display = 'none';
            prodottoFields.style.display = 'block';
        }
    }
}

// Initialize tooltips
function initTooltips() {
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initTooltips();
    
    // Auto-toggle fields for service/product forms
    const tipoField = document.querySelector('select[name="tipo"]');
    if (tipoField) {
        tipoField.addEventListener('change', toggleFields);
        toggleFields(); // Initial toggle
    }
    
    // Auto-focus first input
    const firstInput = document.querySelector('input[type="text"], input[type="email"], textarea, select');
    if (firstInput && !firstInput.value) {
        firstInput.focus();
    }
});

// WebSocket helper for real-time updates
class AutolavaggioWebSocket {
    constructor(url) {
        this.url = url;
        this.socket = null;
        this.reconnectInterval = 3000;
        this.maxReconnectAttempts = 10;
        this.reconnectAttempts = 0;
    }
    
    connect() {
        try {
            this.socket = new WebSocket(this.url);
            
            this.socket.onopen = () => {
                console.log('WebSocket connected');
                this.reconnectAttempts = 0;
            };
            
            this.socket.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            };
            
            this.socket.onclose = () => {
                console.log('WebSocket disconnected');
                this.reconnect();
            };
            
            this.socket.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        } catch (error) {
            console.error('Failed to connect WebSocket:', error);
            this.reconnect();
        }
    }
    
    reconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            setTimeout(() => {
                console.log(`Reconnecting WebSocket (attempt ${this.reconnectAttempts})`);
                this.connect();
            }, this.reconnectInterval);
        }
    }
    
    send(data) {
        if (this.socket && this.socket.readyState === WebSocket.OPEN) {
            this.socket.send(JSON.stringify(data));
        }
    }
    
    handleMessage(data) {
        // Override this method in specific implementations
        console.log('WebSocket message received:', data);
    }
    
    disconnect() {
        if (this.socket) {
            this.socket.close();
        }
    }
}

// Export functions for use in other scripts
window.AutolavaggioJS = {
    fetchWithCSRF,
    showLoading,
    hideLoading,
    showToast,
    formatCurrency,
    formatDate,
    formatDateTime,
    setupAutoRefresh,
    confirmAction,
    toggleFields,
    AutolavaggioWebSocket
};