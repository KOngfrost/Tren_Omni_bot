/* oss-web-panel — общий клиентский скрипт */
(function () {
    'use strict';

    var prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.getAttribute('content')) {
        window.CSRF_TOKEN = meta.getAttribute('content');
        var originalFetch = window.fetch;
        window.fetch = function (url, options) {
            options = options || {};
            options.headers = options.headers || {};
            if (!options.headers['X-CSRF-Token'] && window.CSRF_TOKEN) {
                options.headers['X-CSRF-Token'] = window.CSRF_TOKEN;
            }
            return originalFetch.call(this, url, options);
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

    // Инициализация Store при загрузке страницы
    if (window.Store) {
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

    function openModal(id) {
        var modal = document.getElementById(id);
        if (modal) modal.classList.add('active');
    }
    function closeModal(id) {
        var modal = document.getElementById(id);
        if (modal) modal.classList.remove('active');
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
                icon.textContent = theme === 'light' ? '🌙' : '☀️';
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
            var submitButtons = form.querySelectorAll('button[type="submit"], input[type="submit"]');
            submitButtons.forEach(function (btn) {
                btn.classList.add('is-submitting');
                btn.setAttribute('aria-busy', 'true');
            });

            setTimeout(function () {
                form.dataset.submitting = 'false';
                submitButtons.forEach(function (btn) {
                    btn.classList.remove('is-submitting');
                    btn.removeAttribute('aria-busy');
                });
            }, 8000);
        }
    });

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

        event.preventDefault();
        var input = form.querySelector('input[name="name"]');
        if (!input || !input.value.trim()) return;

        var name = input.value.trim();
        if (window.Store) {
            window.Store.createDepartment(name).then(function (result) {
                if (result.success) {
                    showSuccess('Отдел "' + result.data.name + '" создан');
                    input.value = '';
                    closeModal('create-modal');
                } else {
                    showError(result.error || 'Ошибка создания отдела');
                }
            });
        }
    });

    /**
     * Обработка переименования отдела через API.
     */
    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form.matches('[data-ajax-form="rename-department"]')) return;

        event.preventDefault();
        var deptId = form.dataset.deptId;
        var input = form.querySelector('input[name="name"]');
        if (!input || !input.value.trim() || !deptId) return;

        var name = input.value.trim();
        if (window.Store) {
            window.Store.renameDepartment(parseInt(deptId, 10), name).then(function (result) {
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
            });
        }
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

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initCookieConsent);
    } else {
        initCookieConsent();
    }
})();
