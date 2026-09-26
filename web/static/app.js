/* oss-web-panel — общий клиентский скрипт */
(function () {
    'use strict';

    var prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ==========================================
    // CSRF: динамическое чтение токена + обёртка fetch
    // ==========================================

    /**
     * Актуальный CSRF-токен из мета-тега текущего документа.
     * Читается перед КАЖДЫМ запросом, поэтому переживает ротацию токена
     * и перерисовки DOM (в отличие от значения, захваченного при загрузке).
     */
    function getCsrfToken() {
        var metaTag = document.querySelector('meta[name="csrf-token"]');
        if (metaTag && metaTag.getAttribute('content')) {
            window.CSRF_TOKEN = metaTag.getAttribute('content');
            return window.CSRF_TOKEN;
        }
        return window.CSRF_TOKEN || '';
    }
    window.getCsrfToken = getCsrfToken;

    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) {
        window.CSRF_TOKEN = meta.getAttribute('content');
    }

    // ==========================================
    // Double-Submit Guard: немедленная разблокировка кнопки
    // ==========================================

    /** Форма, заблокированная guard'ом в данный момент (null — нет заблокированных). */
    var guardedForm = null;

    /**
     * Немедленно снять флаг is-submitting и разблокировать кнопку отправки.
     * Без аргумента снимается блокировка с текущей защищённой формы.
     */
    function releaseSubmitGuard(form) {
        var target = (form && form.tagName === 'FORM') ? form : guardedForm;
        if (!target) return;
        target.dataset.submitting = 'false';
        target.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (btn) {
            btn.classList.remove('is-submitting');
            btn.removeAttribute('aria-busy');
        });
        if (guardedForm === target) guardedForm = null;
    }
    window.releaseSubmitGuard = releaseSubmitGuard;

    function _hasCsrfHeader(headers) {
        if (!headers) return false;
        if (typeof Headers !== 'undefined' && headers instanceof Headers) {
            return headers.has('X-CSRF-Token');
        }
        var keys = Object.keys(headers);
        for (var i = 0; i < keys.length; i++) {
            if (keys[i].toLowerCase() === 'x-csrf-token') return true;
        }
        return false;
    }

    if (typeof window.fetch === 'function') {
        var originalFetch = window.fetch;
        window.fetch = function (url, options) {
            options = options || {};
            var token = getCsrfToken();
            if (token && !_hasCsrfHeader(options.headers)) {
                if (typeof Headers !== 'undefined' && options.headers instanceof Headers) {
                    var dynamicHeaders = new Headers(options.headers);
                    dynamicHeaders.set('X-CSRF-Token', token);
                    options.headers = dynamicHeaders;
                } else {
                    options.headers = options.headers || {};
                    options.headers['X-CSRF-Token'] = token;
                }
            }
            return originalFetch.call(this, url, options).then(
                function (response) {
                    // Ответ 4xx/5xx — немедленно разблокируем кнопку формы,
                    // не дожидаясь таймаута guard'а
                    if (response && response.status >= 400) releaseSubmitGuard(null);
                    return response;
                },
                function (error) {
                    // Сетевой сбой — немедленно разблокируем кнопку формы
                    releaseSubmitGuard(null);
                    throw error;
                }
            );
        };
    }

    // ==========================================
    // Инициализация хранилища и бокового меню
    // ==========================================

    /**
     * Обновить боковое меню на основе данных из Store.
     * Вызывается при каждом изменении списка отделов.
     */
    function renderSidebarDepartments(departments) {
        var nav = document.getElementById('sidebar-nav');
        if (!nav) return;

        // Найти или создать контейнер для отделов
        var deptContainer = document.getElementById('sidebar-depts');
        if (!deptContainer) {
            deptContainer = document.createElement('div');
            deptContainer.id = 'sidebar-depts';
            // Вставить после навигационной метки "Отделы"
            var label = nav.querySelector('.nav-label-spaced');
            if (label && label.parentNode) {
                label.parentNode.insertBefore(deptContainer, label.nextSibling);
            } else {
                nav.appendChild(deptContainer);
            }
        }

        // Очистить текущий список
        deptContainer.innerHTML = '';

        // Активный отдел (если на странице фрейма)
        var activeDeptId = null;
        var deptMatch = window.location.pathname.match(/^\/dept\/(\d+)\//);
        if (deptMatch) {
            activeDeptId = parseInt(deptMatch[1], 10);
        }

        if (!departments || departments.length === 0) {
            deptContainer.innerHTML = '';
        } else {
            // Отрисовать каждый отдел
            departments.forEach(function (dept) {
                var link = document.createElement('a');
                var isActive = (activeDeptId === dept.id);
                link.className = 'nav-link' + (isActive ? ' active' : '');
                link.href = '/dept/' + dept.id + '/';
                link.innerHTML = '<span class="ico">◈</span>' + escapeHtml(dept.name);
                deptContainer.appendChild(link);
            });
        }
    }

    /**
     * Экранировать HTML для безопасной вставки.
     */
    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Показать уведомление об ошибке.
     */
    function showError(message) {
        // Удалить предыдущие ошибки
        var existing = document.querySelectorAll('.alert-flash-error');
        existing.forEach(function (el) { el.remove(); });

        var alert = document.createElement('div');
        alert.className = 'alert alert-error alert-flash-error';
        alert.textContent = message;
        alert.style.cssText = 'position:fixed;top:1rem;right:1rem;z-index:9999;max-width:400px;';

        var main = document.querySelector('.main-content');
        if (main) {
            main.insertBefore(alert, main.firstChild);
        } else {
            document.body.appendChild(alert);
        }

        // Авто-скрытие через 5 секунд
        setTimeout(function () {
            alert.style.opacity = '0';
            alert.style.transition = 'opacity 0.3s';
            setTimeout(function () { alert.remove(); }, 300);
        }, 5000);
    }

    /**
     * Показать уведомление об успехе.
     */
    function showSuccess(message) {
        var existing = document.querySelectorAll('.alert-flash-success');
        existing.forEach(function (el) { el.remove(); });

        var alert = document.createElement('div');
        alert.className = 'alert alert-success alert-flash-success';
        alert.textContent = message;
        alert.style.cssText = 'position:fixed;top:1rem;right:1rem;z-index:9999;max-width:400px;';

        var main = document.querySelector('.main-content');
        if (main) {
            main.insertBefore(alert, main.firstChild);
        } else {
            document.body.appendChild(alert);
        }

        setTimeout(function () {
            alert.style.opacity = '0';
            alert.style.transition = 'opacity 0.3s';
            setTimeout(function () { alert.remove(); }, 300);
        }, 3000);
    }

    // Инициализация Store при загрузке страницы (только для авторизованных страниц с сайдбаром)
    if (window.Store && document.getElementById('sidebar')) {
        // Подписаться на изменения отделов для обновления сайдбара
        window.Store.subscribeDepartments(renderSidebarDepartments);

        // Подписаться на ошибки
        window.Store.subscribeErrors(function (error) {
            if (error) showError(error);
        });

        // Загрузить отделы из API (или из кэша)
        window.Store.loadDepartments().then(function (result) {
            if (!result.success) {
                console.warn('Не удалось загрузить отделы:', result.error);
            }
        });
    }

    function syncAdminModalFields() {
        var adminTypeSelect = document.getElementById('admin_type');
        if (adminTypeSelect) {
            var val = adminTypeSelect.value;
            var vkFields = document.getElementById('vk_fields');
            var tempFields = document.getElementById('temp_fields');
            var vkInput = document.getElementById('vk_id');
            if (val === 'temp') {
                if (vkFields) vkFields.style.display = 'none';
                if (tempFields) tempFields.style.display = 'block';
                if (vkInput) vkInput.removeAttribute('required');
            } else {
                if (vkFields) vkFields.style.display = 'block';
                if (tempFields) tempFields.style.display = 'none';
                if (vkInput) vkInput.setAttribute('required', 'required');
            }
        }
        var durationPresetSelect = document.getElementById('duration_preset');
        if (durationPresetSelect) {
            var val = durationPresetSelect.value;
            var customField = document.getElementById('custom_duration_field');
            var customInput = document.getElementById('custom_hours');
            if (val === 'custom') {
                if (customField) customField.style.display = 'block';
                if (customInput) customInput.setAttribute('required', 'required');
            } else {
                if (customField) customField.style.display = 'none';
                if (customInput) customInput.removeAttribute('required');
            }
        }
    }
    window.syncAdminModalFields = syncAdminModalFields;

    document.addEventListener('change', function (e) {
        if (e.target && (e.target.id === 'admin_type' || e.target.id === 'duration_preset')) {
            syncAdminModalFields();
        }
    });

    function openModal(id) {
        var modal = document.getElementById(id);
        if (modal) {
            if (modal.parentElement !== document.body) {
                document.body.appendChild(modal);
            }
            modal.classList.add('active');
            document.body.style.overflow = 'hidden';
            if (id === 'modal') {
                syncAdminModalFields();
            }
        }
    }
    function closeModal(id) {
        var modal = document.getElementById(id);
        if (modal) {
            modal.classList.remove('active');
            if (!document.querySelector('.modal.active')) {
                document.body.style.overflow = '';
            }
        }
    }
    window.openModal = openModal;
    window.closeModal = closeModal;
    // Экспорт тостов дизайн-системы: замена нативного alert() в модулях,
    // у которых нет доступа к локальным функциям (tickets.html и др.)
    window.showError = showError;
    window.showSuccess = showSuccess;

    document.addEventListener('click', function (event) {
        var opener = event.target.closest('[data-open-modal]');
        var closer = event.target.closest('[data-close-modal]');
        if (opener) openModal(opener.dataset.openModal);
        if (closer) closeModal(closer.dataset.closeModal);
        if (event.target.classList && event.target.classList.contains('modal')) {
            event.target.classList.remove('active');
            if (!document.querySelector('.modal.active')) {
                document.body.style.overflow = '';
            }
        }
        var question = event.target.closest('.faq-question');
        if (question) {
            var item = question.closest('.faq-item');
            if (item) item.classList.toggle('open');
        }
        var togglePw = event.target.closest('.toggle-password');
        if (togglePw) {
            window.togglePasswordVisibility(togglePw);
        }
        var errorAction = event.target.closest('[data-error-action]');
        if (errorAction) {
            var action = errorAction.dataset.errorAction;
            if (action === 'reload') window.location.reload();
            if (action === 'back') window.history.back();
        }
        var themeToggle = event.target.closest('[data-action="toggle-theme"]');
        if (themeToggle) {
            event.preventDefault();
            toggleTheme();
        }
    });

    // ==========================================
    // Управление темами (Dark / Light) и Glassmorphism
    // ==========================================
    var THEME_KEY = 'app_theme';
    var GLASS_KEY = 'app_glass_effect';

    function setTheme(theme) {
        if (theme !== 'light' && theme !== 'dark') theme = 'dark';
        document.documentElement.setAttribute('data-theme', theme);
        try {
            localStorage.setItem(THEME_KEY, theme);
            document.cookie = THEME_KEY + '=' + theme + '; path=/; max-age=31536000; SameSite=Lax';
        } catch (e) {}

        var toggles = document.querySelectorAll('[data-action="toggle-theme"]');
        toggles.forEach(function (btn) {
            var icon = btn.querySelector('.theme-icon');
            if (icon) {
                icon.innerHTML = theme === 'light'
                    ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>'
                    : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"/><path d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72l1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>';
            }
            btn.setAttribute('aria-label', theme === 'light' ? 'Переключить на тёмную тему' : 'Переключить на светлую тему');
            btn.setAttribute('title', theme === 'light' ? 'Тёмная тема' : 'Светлая тема');
        });

        var radios = document.querySelectorAll('input[name="theme"]');
        radios.forEach(function (radio) {
            if (radio.value === theme) radio.checked = true;
        });
    }

    function toggleTheme() {
        var current = document.documentElement.getAttribute('data-theme') || 'dark';
        setTheme(current === 'light' ? 'dark' : 'light');
    }

    function setGlassEffect(enabled) {
        if (enabled) {
            document.documentElement.classList.remove('disable-glass');
        } else {
            document.documentElement.classList.add('disable-glass');
        }
        try {
            localStorage.setItem(GLASS_KEY, enabled ? 'true' : 'false');
            document.cookie = GLASS_KEY + '=' + (enabled ? 'true' : 'false') + '; path=/; max-age=31536000; SameSite=Lax';
        } catch (e) {}
    }

    window.setTheme = setTheme;
    window.toggleTheme = toggleTheme;
    window.setGlassEffect = setGlassEffect;

    var SVG_EYE = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
    var SVG_EYE_OFF = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>';

    document.addEventListener('change', function (event) {
        var switcher = event.target.closest('#dept-switcher');
        if (switcher && switcher.value) window.location.href = switcher.value;

        var tableFilter = event.target.closest('[data-filter-table]');
        if (tableFilter) {
            var selectedDept = tableFilter.value;
            var container = tableFilter.closest('.card') || document;
            var rows = container.querySelectorAll('[data-dept]');
            rows.forEach(function (row) {
                if (!selectedDept || row.dataset.dept === selectedDept) {
                    row.style.display = '';
                } else {
                    row.style.display = 'none';
                }
            });
        }
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            document.querySelectorAll('.modal.active').forEach(function (modal) {
                modal.classList.remove('active');
            });
            document.body.style.overflow = '';
        }
    });

    document.addEventListener('submit', function (event) {
        var form = event.target;
        var message = form.dataset ? form.dataset.confirm : null;
        if (message && !window.confirm(message)) {
            event.preventDefault();
            return false;
        }

        // Global double-submit guard
        if (form && form.tagName === 'FORM') {
            if (form.dataset.submitting === 'true') {
                event.preventDefault();
                return false;
            }
            form.dataset.submitting = 'true';
            guardedForm = form;
            var submitButtons = form.querySelectorAll('button[type="submit"], input[type="submit"]');
            submitButtons.forEach(function (btn) {
                btn.classList.add('is-submitting');
                btn.setAttribute('aria-busy', 'true');
            });

            // Страховочный таймаут: основная разблокировка происходит
            // немедленно — при ошибке валидации (invalid), сетевом сбое
            // или ответе 4xx/5xx (см. releaseSubmitGuard / обёртку fetch).
            setTimeout(function () {
                releaseSubmitGuard(form);
            }, 8000);
        }
    });

    // Ошибка HTML5-валидации — немедленно снимаем блокировку кнопки,
    // иначе форма останется заблокированной до таймаута guard'а
    document.addEventListener('invalid', function (event) {
        var form = event.target && event.target.form;
        if (form) releaseSubmitGuard(form);
    }, true);

    window.toggleAnswer = function () {
        var checkbox = document.getElementById('is_final');
        var answerGroup = document.getElementById('answer-group');
        var answerTextarea = document.getElementById('final_answer');
        if (!checkbox || !answerGroup) return;
        var show = checkbox.checked;
        answerGroup.classList.toggle('is-hidden', !show);
        if (answerTextarea) answerTextarea.required = show;
    };

    window.togglePasswordVisibility = function (btn) {
        var input = document.getElementById('password');
        if (!btn) btn = document.querySelector('.toggle-password');
        if (!input || !btn) return;
        if (input.type === 'password') {
            input.type = 'text';
            btn.setAttribute('aria-label', 'Скрыть пароль');
            var eye = btn.querySelector('.icon-eye-open');
            if (eye) eye.innerHTML = SVG_EYE_OFF;
        } else {
            input.type = 'password';
            btn.setAttribute('aria-label', 'Показать пароль');
            var eye2 = btn.querySelector('.icon-eye-open');
            if (eye2) eye2.innerHTML = SVG_EYE;
        }
    };

    document.addEventListener('change', function (event) {
        if (event.target.matches('[data-toggle-answer]')) window.toggleAnswer();
    });

    // ==========================================
    // Управление боковым меню (Mobile & Desktop)
    // ==========================================

    function toggleSidebar(forceState) {
        var sidebar = document.getElementById('sidebar');
        var container = document.querySelector('.container');
        var backdrop = document.getElementById('mobile-backdrop') || document.querySelector('.mobile-menu-backdrop');
        var toggleBtns = document.querySelectorAll('.mobile-nav-toggle, #menu-toggle-btn');
        if (!sidebar) return;

        var isMobile = window.innerWidth <= 900;

        if (isMobile) {
            var open = (forceState !== undefined) ? forceState : !sidebar.classList.contains('is-open');
            sidebar.classList.toggle('is-open', open);
            if (backdrop) backdrop.classList.toggle('is-active', open);
            document.body.classList.toggle('no-scroll', open);
            toggleBtns.forEach(function (btn) {
                btn.setAttribute('aria-expanded', String(open));
            });
        } else {
            // На десктопе сворачиваем / разворачиваем меню
            var isCollapsed = (forceState !== undefined) ? !forceState : !container.classList.contains('sidebar-collapsed');
            if (container) container.classList.toggle('sidebar-collapsed', isCollapsed);
            toggleBtns.forEach(function (btn) {
                btn.setAttribute('aria-expanded', String(!isCollapsed));
            });
            try {
                localStorage.setItem('oss_sidebar_collapsed', isCollapsed ? '1' : '0');
            } catch (_) {}
        }
    }
    window.toggleSidebar = toggleSidebar;

    // Восстанавливаем состояние меню на десктопе при загрузке
    try {
        if (window.innerWidth > 900 && localStorage.getItem('oss_sidebar_collapsed') === '1') {
            var cont = document.querySelector('.container');
            if (cont) cont.classList.add('sidebar-collapsed');
        }
    } catch (_) {}

    function handleMenuClick(event) {
        var toggle = event.target.closest('.mobile-nav-toggle, #menu-toggle-btn');
        var closeBtn = event.target.closest('#sidebar-close-btn, .sidebar-close-btn');
        var backdrop = event.target.closest('.mobile-menu-backdrop, #mobile-backdrop');

        if (toggle) {
            event.preventDefault();
            toggleSidebar();
            return;
        }
        if (closeBtn || backdrop) {
            event.preventDefault();
            toggleSidebar(false);
            return;
        }

        // На мобильных при клике на пункт меню закрываем шторку
        if (window.innerWidth <= 900 && event.target.closest('.sidebar-nav .nav-link')) {
            toggleSidebar(false);
        }
    }

    document.addEventListener('click', handleMenuClick);

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            toggleSidebar(false);
        }
    });

    window.addEventListener('resize', function () {
        if (window.innerWidth > 900) {
            var sb = document.getElementById('sidebar');
            var bd = document.getElementById('mobile-backdrop') || document.querySelector('.mobile-menu-backdrop');
            if (sb) sb.classList.remove('is-open');
            if (bd) bd.classList.remove('is-active');
            document.body.classList.remove('no-scroll');
        }
    });

    document.addEventListener('click', function (event) {
        var tab = event.target.closest('[data-tab]');
        if (tab && tab.closest('[data-tabs]')) {
            var container = tab.closest('[data-tabs]');
            var tabName = tab.dataset.tab;
            container.querySelectorAll('.tab-link').forEach(function (link) {
                link.classList.remove('active');
            });
            tab.classList.add('active');
            container.querySelectorAll('[data-tab-panel]').forEach(function (panel) {
                panel.classList.remove('active');
            });
            var panel = container.querySelector('[data-tab-panel="' + tabName + '"]');
            if (panel) panel.classList.add('active');
        }
    });

    function animateCounter(el) {
        var target = parseInt(el.textContent, 10) || 0;
        if (target === 0 || prefersReducedMotion) return;
        var duration = 900;
        var start = null;
        function step(ts) {
            if (!start) start = ts;
            var progress = Math.min((ts - start) / duration, 1);
            var eased = 1 - Math.pow(1 - progress, 3);
            el.textContent = Math.round(target * eased);
            if (progress < 1) requestAnimationFrame(step);
        }
        el.textContent = '0';
        requestAnimationFrame(step);
    }

    document.querySelectorAll('[data-counter]').forEach(function (el, i) {
        setTimeout(function () { animateCounter(el); }, 150 + i * 100);
    });

    // ==========================================
    // AJAX-обработчики для страницы отделов
    // ==========================================

    /**
     * Обработка создания отдела через API (без перезагрузки).
     */
    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form.matches('[data-ajax-form="create-department"]')) return;

        // store.js недоступен/заблокирован — НЕ перехватываем submit:
        // форма выполняет стандартный серверный POST (method/action в шаблоне)
        if (!window.Store) return;

        event.preventDefault();
        var input = form.querySelector('input[name="name"]');
        var name = input ? input.value.trim() : '';
        if (!name) {
            // Ошибка валидации — немедленно снимаем guard, без ожидания таймаута
            releaseSubmitGuard(form);
            showError('Укажите название отдела');
            if (input) input.focus();
            return;
        }

        window.Store.createDepartment(name).then(function (result) {
            releaseSubmitGuard(form);
            if (result.success) {
                showSuccess('Отдел "' + result.data.name + '" создан');
                if (input) input.value = '';
                closeModal('create-modal');
            } else {
                showError(result.error || 'Ошибка создания отдела');
            }
        }).catch(function () {
            releaseSubmitGuard(form);
            showError('Не удалось создать отдел: ошибка сети');
        });
    });

    /**
     * Обработка переименования отдела через API.
     */
    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form.matches('[data-ajax-form="rename-department"]')) return;

        // store.js недоступен/заблокирован — НЕ перехватываем submit:
        // форма выполняет стандартный серверный POST (method/action в шаблоне)
        if (!window.Store) return;

        event.preventDefault();
        var deptId = form.dataset.deptId;
        var input = form.querySelector('input[name="name"]');
        var name = input ? input.value.trim() : '';
        if (!name || !deptId) {
            // Ошибка валидации — немедленно снимаем guard, без ожидания таймаута
            releaseSubmitGuard(form);
            showError('Укажите новое название отдела');
            if (input) input.focus();
            return;
        }

        window.Store.renameDepartment(parseInt(deptId, 10), name).then(function (result) {
            releaseSubmitGuard(form);
            if (result.success) {
                showSuccess('Отдел переименован в "' + result.data.name + '"');
                // Закрыть модалку
                var modal = form.closest('.modal');
                if (modal) modal.classList.remove('active');
                // Обновить карточку на странице
                updateDepartmentCard(result.data);
            } else {
                showError(result.error || 'Ошибка переименования');
            }
        }).catch(function () {
            releaseSubmitGuard(form);
            showError('Не удалось переименовать отдел: ошибка сети');
        });
    });

    /**
     * Обработка удаления отдела через API.
     */
    document.addEventListener('click', function (event) {
        var btn = event.target.closest('[data-ajax-delete]');
        if (!btn) return;

        var deptId = btn.dataset.deptId;
        var deptName = btn.dataset.deptName;
        if (!deptId) return;

        if (!window.confirm('Удалить отдел "' + (deptName || '') + '"? Удалить можно только пустой отдел.')) return;

        if (window.Store) {
            window.Store.deleteDepartment(parseInt(deptId, 10)).then(function (result) {
                if (result.success) {
                    showSuccess('Отдел удалён');
                    // Удалить карточку из DOM
                    var card = document.querySelector('[data-dept-card="' + deptId + '"]');
                    if (card) {
                        card.style.transition = 'opacity 0.3s, transform 0.3s';
                        card.style.opacity = '0';
                        card.style.transform = 'scale(0.9)';
                        setTimeout(function () { card.remove(); }, 300);
                    }
                } else {
                    showError(result.error || 'Ошибка удаления');
                }
            });
        }
    });

    /**
     * Обновить карточку отдела в DOM после переименования.
     */
    function updateDepartmentCard(dept) {
        var card = document.querySelector('[data-dept-card="' + dept.id + '"]');
        if (!card) return;
        var nameEl = card.querySelector('.department-name');
        if (nameEl) nameEl.textContent = dept.name;
        var idEl = card.querySelector('.department-id');
        if (idEl) idEl.textContent = 'ID: ' + dept.id;
    }

    /**
     * Закрытие предупреждающего баннера безопасности (bootstrap) с анимацией
     */
    document.addEventListener('click', function (event) {
        var closeBtn = event.target.closest('#close-security-banner-btn, [data-dismiss="security-banner"]');
        if (!closeBtn) return;
        var banner = document.getElementById('bootstrap-security-banner') || closeBtn.closest('.security-banner');
        if (banner) {
            banner.classList.add('is-hiding');
            try {
                sessionStorage.setItem('dismiss_bootstrap_banner', '1');
            } catch (e) {}
            setTimeout(function () {
                banner.remove();
            }, 300);
        }
    });

    // Проверка сохранённого состояния при инициализации
    try {
        if (sessionStorage.getItem('dismiss_bootstrap_banner') === '1') {
            var bannerEl = document.getElementById('bootstrap-security-banner');
            if (bannerEl) bannerEl.style.display = 'none';
        }
    } catch (e) {}

    /**
     * Баннер согласия на использование файлов cookie (152-ФЗ)
     */
    function initCookieConsent() {
        var banner = document.getElementById('cookie-consent-banner');
        if (!banner) return;

        try {
            if (localStorage.getItem('cookie_consent_accepted') === 'true') {
                banner.style.display = 'none';
                return;
            }
        } catch (e) {}

        banner.style.display = 'flex';

        var acceptBtn = document.getElementById('cookie-consent-accept-btn');
        if (acceptBtn) {
            acceptBtn.addEventListener('click', function () {
                try {
                    localStorage.setItem('cookie_consent_accepted', 'true');
                } catch (e) {}
                banner.classList.add('is-hiding');
                setTimeout(function () {
                    banner.style.display = 'none';
                }, 300);
            });
        }
    }

    /**
     * Вспомогательная функция безопасного экранирования HTML
     */
    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    /**
     * Счётчики уведомлений навигации и Центр уведомлений (раздельные счётчики и прямые ссылки)
     */
    function initNavCounters() {
        var ticketsBadge = document.getElementById('nav-badge-tickets');
        var repliesBadge = document.getElementById('nav-badge-replies');
        var partBadge = document.getElementById('nav-badge-partnerships');
        var totalBadge = document.getElementById('notifications-badge-total');
        var notifBtn = document.getElementById('notifications-btn');
        var dropdown = document.getElementById('notifications-dropdown');
        var itemsList = document.getElementById('notifications-items-list');
        var tagTickets = document.getElementById('tag-new-tickets');
        var tagReplies = document.getElementById('tag-student-replies');
        var tagPart = document.getElementById('tag-partnerships');
        var notifWrapper = document.getElementById('notifications-wrapper');

        var directTicketUrl = null;
        var directReplyUrl = null;
        var directPartnerUrl = null;

        // Прямой переход при клике на бейдж в боковом меню (если ровно 1 элемент)
        if (ticketsBadge) {
            ticketsBadge.addEventListener('click', function (e) {
                if (directTicketUrl) {
                    e.preventDefault();
                    e.stopPropagation();
                    window.location.href = directTicketUrl;
                }
            });
        }
        if (repliesBadge) {
            repliesBadge.addEventListener('click', function (e) {
                if (directReplyUrl) {
                    e.preventDefault();
                    e.stopPropagation();
                    window.location.href = directReplyUrl;
                }
            });
        }
        if (partBadge) {
            partBadge.addEventListener('click', function (e) {
                if (directPartnerUrl) {
                    e.preventDefault();
                    e.stopPropagation();
                    window.location.href = directPartnerUrl;
                }
            });
        }

        // Интерактивность выпадающего окна Центра уведомлений
        if (notifBtn && dropdown) {
            notifBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                var isOpen = dropdown.classList.contains('active');
                if (isOpen) {
                    dropdown.classList.remove('active');
                    notifBtn.setAttribute('aria-expanded', 'false');
                } else {
                    dropdown.classList.add('active');
                    notifBtn.setAttribute('aria-expanded', 'true');
                    updateCounters();
                }
            });

            document.addEventListener('click', function (e) {
                if (notifWrapper && !notifWrapper.contains(e.target)) {
                    if (dropdown.classList.contains('active')) {
                        dropdown.classList.remove('active');
                        notifBtn.setAttribute('aria-expanded', 'false');
                    }
                }
            });

            document.addEventListener('keydown', function (e) {
                if (e.key === 'Escape' && dropdown.classList.contains('active')) {
                    dropdown.classList.remove('active');
                    notifBtn.setAttribute('aria-expanded', 'false');
                    notifBtn.focus();
                }
            });
        }

        function updateCounters() {
            fetch('/api/counters')
                .then(function (res) {
                    return res.ok ? res.json() : null;
                })
                .then(function (result) {
                    if (!result || !result.success || !result.data) return;
                    var data = result.data;

                    directTicketUrl = data.new_ticket_direct_url || null;
                    directReplyUrl = data.student_reply_direct_url || null;
                    directPartnerUrl = data.partnership_direct_url || null;

                    var newTickets = data.new_tickets || 0;
                    var studentReplies = data.student_replies || 0;
                    var newParts = data.new_partnerships || 0;
                    var totalCount = data.total_notifications || (newTickets + studentReplies + newParts);

                    // 1. Общий бейдж на колокольчике
                    if (totalBadge) {
                        if (totalCount > 0) {
                            totalBadge.textContent = totalCount > 99 ? '99+' : totalCount;
                            totalBadge.classList.remove('hidden');
                        } else {
                            totalBadge.classList.add('hidden');
                        }
                    }

                    // 2. Раздельные бейджи в боковом меню
                    if (ticketsBadge) {
                        if (newTickets > 0) {
                            ticketsBadge.textContent = '🔥 ' + newTickets;
                            ticketsBadge.title = newTickets === 1 ? 'Нажмите для перехода к новой заявке' : 'Новые нерассмотренные заявки (' + newTickets + ')';
                            ticketsBadge.classList.remove('hidden');
                        } else {
                            ticketsBadge.classList.add('hidden');
                        }
                    }
                    if (repliesBadge) {
                        if (studentReplies > 0) {
                            repliesBadge.textContent = '💬 ' + studentReplies;
                            repliesBadge.title = studentReplies === 1 ? 'Нажмите для перехода к ответу студента' : 'Новые ответы студентов (' + studentReplies + ')';
                            repliesBadge.classList.remove('hidden');
                        } else {
                            repliesBadge.classList.add('hidden');
                        }
                    }
                    if (partBadge) {
                        if (newParts > 0) {
                            partBadge.textContent = '🤝 ' + newParts;
                            partBadge.title = newParts === 1 ? 'Нажмите для перехода к заявке на партнёрство' : 'Заявки на партнёрство (' + newParts + ')';
                            partBadge.classList.remove('hidden');
                        } else {
                            partBadge.classList.add('hidden');
                        }
                    }

                    // 3. Теги в шапке выпадающего списка
                    if (tagTickets) {
                        if (newTickets > 0) {
                            tagTickets.textContent = '🔥 ' + newTickets + ' новых заявок';
                            tagTickets.classList.remove('hidden');
                        } else {
                            tagTickets.classList.add('hidden');
                        }
                    }
                    if (tagReplies) {
                        if (studentReplies > 0) {
                            tagReplies.textContent = '💬 ' + studentReplies + ' ответов';
                            tagReplies.classList.remove('hidden');
                        } else {
                            tagReplies.classList.add('hidden');
                        }
                    }
                    if (tagPart) {
                        if (newParts > 0) {
                            tagPart.textContent = '🤝 ' + newParts + ' партнёрств';
                            tagPart.classList.remove('hidden');
                        } else {
                            tagPart.classList.add('hidden');
                        }
                    }

                    // 4. Список конкретных уведомлений со ссылками на тикеты
                    if (itemsList && data.items) {
                        if (data.items.length === 0) {
                            itemsList.innerHTML = '<div class="notifications-empty">✨ Нет новых уведомлений</div>';
                        } else {
                            var html = '';
                            for (var i = 0; i < data.items.length; i++) {
                                var item = data.items[i];
                                html += '<a href="' + escapeHtml(item.url) + '" class="notifications-item notif-type-' + escapeHtml(item.type) + '">' +
                                    '<div class="notifications-item-icon" aria-hidden="true">' + escapeHtml(item.icon) + '</div>' +
                                    '<div class="notifications-item-content">' +
                                        '<div class="notifications-item-title-row">' +
                                            '<span class="notifications-item-title">' + escapeHtml(item.title) + '</span>' +
                                            '<span class="notifications-item-badge badge-' + escapeHtml(item.type) + '">' + escapeHtml(item.type_label) + '</span>' +
                                        '</div>' +
                                        (item.department ? '<div class="notifications-item-dept">' + escapeHtml(item.department) + '</div>' : '') +
                                        (item.text ? '<div class="notifications-item-text">' + escapeHtml(item.text) + '</div>' : '') +
                                        (item.time ? '<div class="notifications-item-time">' + escapeHtml(item.time) + '</div>' : '') +
                                    '</div>' +
                                '</a>';
                            }
                            itemsList.innerHTML = html;
                        }
                    }
                })
                .catch(function () {});
        }

        // Первичная загрузка и периодический опрос каждые 20 секунд
        updateCounters();
        setInterval(updateCounters, 20000);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            initCookieConsent();
            initNavCounters();
        });
    } else {
        initCookieConsent();
        initNavCounters();
    }
})();
