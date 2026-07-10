/* ── App.js — 全局 JavaScript ─────────────── */

// HTMX CSRF 配置 (Django)
document.body.addEventListener('htmx:configRequest', (e) => {
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || getCookie('csrftoken');
    if (csrfToken) {
        e.detail.headers['X-CSRFToken'] = csrfToken;
    }
});

function getCookie(name) {
    let val = null;
    document.cookie.split(';').forEach(c => {
        c = c.trim();
        if (c.startsWith(name + '=')) {
            val = decodeURIComponent(c.substring(name.length + 1));
        }
    });
    return val;
}

// 全局 Toast 辅助函数
function showToast(msg, type = 'success') {
    window.dispatchEvent(new CustomEvent('toast', { detail: { msg, type } }));
}

// HTMX 请求完成后自动显示 toast
document.body.addEventListener('htmx:afterRequest', (e) => {
    const resp = e.detail.xhr;
    if (resp && resp.status >= 200 && resp.status < 300) {
        const trigger = resp.getResponseHeader('HX-Trigger');
        if (trigger) {
            try {
                const data = JSON.parse(trigger);
                if (data.showToast) {
                    showToast(data.showToast.msg, data.showToast.type);
                }
            } catch (_) {}
        }
    }
});

// ── 横向滚动标签栏：渐变遮罩 + 选中项居中 + PC适配 ──
(function() {
    var DRAG_THRESHOLD = 5;

    function initSegmentScroll() {
        document.querySelectorAll('.segment-scroll-wrapper').forEach(function(wrapper) {
            var seg = wrapper.querySelector('.ios-segment');
            if (!seg) return;

            // ── 渐变遮罩更新 ──
            function update() {
                var sl = seg.scrollLeft, sw = seg.scrollWidth, cw = seg.clientWidth;
                wrapper.classList.toggle('can-scroll-left', sl > 2);
                wrapper.classList.toggle('can-scroll-right', sl + cw < sw - 2);
            }

            seg.addEventListener('scroll', update, {passive: true});
            update();

            // 选中项居中
            var active = seg.querySelector('.ios-segment-btn.active');
            if (active) {
                active.scrollIntoView({inline: 'center', block: 'nearest', behavior: 'instant'});
                requestAnimationFrame(update);
            }

            // ── 鼠标滚轮 → 横向滚动 ──
            seg.addEventListener('wheel', function(e) {
                if (e.deltaY === 0) return;
                e.preventDefault();
                seg.scrollLeft += e.deltaY;
            }, {passive: false});

            // ── PC 鼠标拖拽滚动 ──
            var isDragging = false;
            var hasDragged = false;
            var startX = 0;
            var scrollStart = 0;

            // 禁止链接原生拖拽
            seg.querySelectorAll('a').forEach(function(a) { a.draggable = false; });
            seg.addEventListener('dragstart', function(e) { e.preventDefault(); });

            seg.addEventListener('mousedown', function(e) {
                if (e.button !== 0) return;
                isDragging = true;
                hasDragged = false;
                startX = e.clientX;
                scrollStart = seg.scrollLeft;
                seg.classList.add('is-grabbing');
                e.preventDefault();
            });

            document.addEventListener('mousemove', function(e) {
                if (!isDragging) return;
                var dx = e.clientX - startX;
                if (Math.abs(dx) > DRAG_THRESHOLD) hasDragged = true;
                seg.scrollLeft = scrollStart - dx;
            });

            document.addEventListener('mouseup', function() {
                if (!isDragging) return;
                isDragging = false;
                seg.classList.remove('is-grabbing');
            });

            // 拖拽后阻止链接跳转
            seg.addEventListener('click', function(e) {
                if (hasDragged) {
                    e.preventDefault();
                    hasDragged = false;
                }
            }, true);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSegmentScroll);
    } else {
        initSegmentScroll();
    }
})();
