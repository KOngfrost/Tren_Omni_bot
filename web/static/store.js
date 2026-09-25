/**
 * Единое хранилище состояния (Global Store) для веб-панели.
 *
 * Реализует паттерн "Единый источник правды" (Single Source of Truth)
 * для отделов и других данных, требующих сквозной реактивности.
 *
 * Особенности:
 * - Подписчики (subscribers) уведомляются при изменении данных
 * - Автоматическая синхронизация с API при операциях CRUD
 * - Персистентность в sessionStorage для сохранения между навигацией
 * - Обработка ошибок с информативными сообщениями
 */
(function () {
    'use strict';

    var _state = {
        departments: [],
        loading: false,
        error: null,
        initialized: false
    };

    var _subscribers = {
        departments: [],
        error: [],
        loading: []
    };

    var STORAGE_KEY = 'oss_panel_state';

    function subscribeDepartments(callback) {
        if (typeof callback !== 'function') return function () {};
        _subscribers.departments.push(callback);
        callback(_state.departments);
        return function () {
            _subscribers.departments = _subscribers.departments.filter(function (cb) {
                return cb !== callback;
            });
        };
    }

    function subscribeErrors(callback) {
        if (typeof callback !== 'function') return function () {};
        _subscribers.error.push(callback);
        return function () {
            _subscribers.error = _subscribers.error.filter(function (cb) {
                return cb !== callback;
            });
        };
    }

    function subscribeLoading(callback) {
        if (typeof callback !== 'function') return function () {};
        _subscribers.loading.push(callback);
        callback(_state.loading);
        return function () {
            _subscribers.loading = _subscribers.loading.filter(function (cb) {
                return cb !== callback;
            });
        };
    }

    function _notify(event, data) {
        if (_subscribers[event]) {
            _subscribers[event].forEach(function (cb) {
                try { cb(data); } catch (e) { /* ignore */ }
            });
        }
    }

    function _setState(patch) {
        var prev = Object.assign({}, _state);
        Object.assign(_state, patch);
        if (patch.departments !== undefined && patch.departments !== prev.departments) {
            _notify('departments', _state.departments);
            _persist();
        }
        if (patch.error !== undefined && patch.error !== prev.error) {
            _notify('error', _state.error);
        }
        if (patch.loading !== undefined && patch.loading !== prev.loading) {
            _notify('loading', _state.loading);
        }
    }

    function _persist() {
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
                departments: _state.departments,
                timestamp: Date.now()
            }));
        } catch (e) { /* sessionStorage недоступен */ }
    }

    function _restore() {
        try {
            var raw = sessionStorage.getItem(STORAGE_KEY);
            if (raw) {
                var parsed = JSON.parse(raw);
                if (parsed.departments && Date.now() - parsed.timestamp < 300000) {
                    _state.departments = parsed.departments;
                    _state.initialized = true;
                    return true;
                }
            }
        } catch (e) { /* ошибка парсинга */ }
        return false;
    }

    /**
     * Актуальный CSRF-токен: читается из мета-тега текущего документа
     * перед КАЖДЫМ запросом (переживает ротацию токена), fallback —
     * значение из глобального окна (задаёт app.js).
     */
    function _csrfToken() {
        var metaTag = document.querySelector('meta[name="csrf-token"]');
        if (metaTag && metaTag.getAttribute('content')) {
            window.CSRF_TOKEN = metaTag.getAttribute('content');
        }
        return window.CSRF_TOKEN || '';
    }

    async function _apiRequest(url, options) {
        options = options || {};
        var headers = options.headers || {};
        headers['Content-Type'] = 'application/json';
        headers['Accept'] = 'application/json';
        var token = _csrfToken();
        if (token) {
            headers['X-CSRF-Token'] = token;
        }
        try {
            var response = await fetch(url, Object.assign({}, options, { headers: headers }));
            var contentType = response.headers.get('content-type') || '';
            if (!response.ok) {
                if (contentType.includes('application/json')) {
                    var json = await response.json();
                    var detail = json.detail || json.error || ('HTTP ' + response.status);
                    if (response.status === 403 && /csrf/i.test(String(detail))) {
                        // Понятное сообщение вместо тихого зависания интерфейса
                        return { success: false, error: 'Сессия обновлена, повторите действие' };
                    }
                    return { success: false, error: detail };
                } else {
                    var text = await response.text();
                    return {
                        success: false,
                        error: 'HTTP ' + response.status + ': Сервер вернул ошибку (' + (contentType.split(';')[0].trim() || 'неизвестный тип') + ')'
                    };
                }
            }
            if (!contentType.includes('application/json')) {
                return { success: false, error: 'Неожиданный тип ответа сервера: ' + (contentType.split(';')[0].trim() || 'неизвестный тип') };
            }
            var json = await response.json();
            return json;
        } catch (networkError) {
            return {
                success: false,
                error: 'Ошибка сети: ' + (networkError.message || 'нет соединения с сервером')
            };
        }
    }

    function getDepartments() {
        return _state.departments || [];
    }

    function hasDepartments() {
        return _state.departments && _state.departments.length > 0;
    }

    async function loadDepartments() {
        if (!_state.initialized) {
            _restore();
            if (_state.departments.length > 0) {
                _notify('departments', _state.departments);
            }
        }
        _setState({ loading: true, error: null });
        var result = await _apiRequest('/api/departments/');
        if (result.success) {
            _setState({ departments: result.data || [], loading: false, initialized: true });
        } else {
            _setState({ loading: false, error: result.error });
        }
        return result;
    }

    async function createDepartment(name) {
        _setState({ loading: true, error: null });
        var result = await _apiRequest('/api/departments/create', {
            method: 'POST',
            body: JSON.stringify({ name: name })
        });
        if (result.success) {
            var updated = _state.departments.concat([result.data]).sort(function (a, b) {
                return a.name.localeCompare(b.name);
            });
            _setState({ departments: updated, loading: false });
        } else {
            _setState({ loading: false, error: result.error });
        }
        return result;
    }

    async function renameDepartment(id, name) {
        _setState({ loading: true, error: null });
        var result = await _apiRequest('/api/departments/' + id + '/rename', {
            method: 'POST',
            body: JSON.stringify({ name: name })
        });
        if (result.success) {
            var updated = _state.departments.map(function (d) {
                return d.id === id ? Object.assign({}, d, { name: result.data.name }) : d;
            }).sort(function (a, b) {
                return a.name.localeCompare(b.name);
            });
            _setState({ departments: updated, loading: false });
        } else {
            _setState({ loading: false, error: result.error });
        }
        return result;
    }

    async function deleteDepartment(id) {
        _setState({ loading: true, error: null });
        var result = await _apiRequest('/api/departments/' + id + '/delete', {
            method: 'POST',
            body: JSON.stringify({})
        });
        if (result.success) {
            var updated = _state.departments.filter(function (d) {
                return d.id !== id;
            });
            _setState({ departments: updated, loading: false });
        } else {
            _setState({ loading: false, error: result.error });
        }
        return result;
    }

    async function refresh() {
        return loadDepartments();
    }

    window.Store = {
        subscribeDepartments: subscribeDepartments,
        subscribeErrors: subscribeErrors,
        subscribeLoading: subscribeLoading,
        getDepartments: getDepartments,
        hasDepartments: hasDepartments,
        loadDepartments: loadDepartments,
        createDepartment: createDepartment,
        renameDepartment: renameDepartment,
        deleteDepartment: deleteDepartment,
        refresh: refresh
    };
})();
