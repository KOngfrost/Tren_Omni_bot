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
        var switcher = event.target.closest('#dept-switcher');
        if (switcher && switcher.value) {
            window.location.href = switcher.value;
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
        var message = event.target.dataset.confirm;
        if (message && !window.confirm(message)) event.preventDefault();
    });

    window.filterTable = function () {
        var filter = document.querySelector('[data-filter-table]');
        if (!filter) return;
        var deptFilter = filter.value;
        document.querySelectorAll('[data-dept]').forEach(function (row) {
            var dept = row.dataset.dept || '';
            row.style.display = (!deptFilter || dept === deptFilter) ? '' : 'none';
        });
    };

    window.toggleAnswer = function () {
        var checkbox = document.getElementById('is_final');
        var answerGroup = document.getElementById('answer-group');
        var answerTextarea = document.getElementById('final_answer');
        if (!checkbox || !answerGroup) return;
        var show = checkbox.checked;
        answerGroup.classList.toggle('is-hidden', !show);
        if (answerTextarea) answerTextarea.required = show;
    };

    window.togglePasswordVisibility = function () {
        var input = document.getElementById('password');
        var btn = document.querySelector('.toggle-password');
        if (!input || !btn) return;
        if (input.type === 'password') {
            input.type = 'text';
            btn.setAttribute('aria-label', 'Скрыть пароль');
            btn.querySelector('.icon-eye-open').textContent = '🙈';
        } else {
            input.type = 'password';
            btn.setAttribute('aria-label', 'Показать пароль');
            btn.querySelector('.icon-eye-open').textContent = '👁';
        }
    };

    document.addEventListener('change', function (event) {
        if (event.target.matches('[data-filter-table]')) window.filterTable();
        if (event.target.matches('[data-toggle-answer]')) window.toggleAnswer();
    });

    document.addEventListener('click', function (event) {
        var toggle = event.target.closest('.mobile-nav-toggle');
        var backdrop = event.target.closest('.mobile-menu-backdrop');
        if (toggle) {
            var sidebar = document.getElementById('sidebar');
            var expanded = toggle.getAttribute('aria-expanded') === 'true';
            if (sidebar) sidebar.classList.toggle('is-open', !expanded);
            toggle.setAttribute('aria-expanded', String(!expanded));
        }
        if (backdrop) {
            var sb = document.getElementById('sidebar');
            var btn = document.querySelector('.mobile-nav-toggle');
            if (sb) sb.classList.remove('is-open');
            if (btn) btn.setAttribute('aria-expanded', 'false');
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
})();
