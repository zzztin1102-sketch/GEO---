/**
 * GEO 生文审核系统 - 前端主应用
 * 纯 HTML/CSS/JS 实现，无前端框架
 *
 * 模块组成：
 *   1. CONFIG    - 全局配置
 *   2. ApiClient - API 客户端封装
 *   3. Auth      - 认证管理
 *   4. UI        - UI 通用组件
 *   5. Pages     - 页面渲染器
 *   6. Router     - hash 路由系统
 *   7. App       - 应用主体（布局、初始化）
 */

/* ============================================================
 * 1. 全局配置
 * ============================================================ */
const CONFIG = {
    API_BASE: '/api/v1',
    TOKEN_KEY: 'geo_review_token',
    USER_KEY: 'geo_review_user',
    DEFAULT_PAGE_SIZE: 10,
    SUPPORTED_CONTENT_FORMATS: ['pdf', 'docx', 'doc', 'txt'],
    SUPPORTED_SUBMISSION_FORMATS: ['xlsx', 'xls', 'docx', 'doc', 'pdf', 'txt'],
    // 是否启用登录认证；设为 false 时打开网页即可直接使用
    AUTH_ENABLED: false,
};

/* ============================================================
 * 2. API 客户端 — 封装 fetch，自动添加 Authorization
 * ============================================================ */
const ApiClient = {
    /** 获取当前存储的 token */
    getToken() {
        return localStorage.getItem(CONFIG.TOKEN_KEY) || null;
    },

    /** 设置 token */
    setToken(token) {
        if (token) {
            localStorage.setItem(CONFIG.TOKEN_KEY, token);
        } else {
            localStorage.removeItem(CONFIG.TOKEN_KEY);
        }
    },

    /** 清除 token */
    clearToken() {
        localStorage.removeItem(CONFIG.TOKEN_KEY);
    },

    /**
     * 统一请求方法
     * @param {string} method - HTTP 方法
     * @param {string} path - API 路径（不含 /api/v1 前缀）
     * @param {object} options - { params, body, isForm }
     * @returns {Promise<any>}
     */
    async request(method, path, { params, body, isForm } = {}) {
        let url = CONFIG.API_BASE + path;

        // 拼接查询参数
        if (params && typeof params === 'object') {
            const search = new URLSearchParams();
            Object.entries(params).forEach(([k, v]) => {
                if (v !== null && v !== undefined && v !== '') {
                    search.append(k, v);
                }
            });
            const qs = search.toString();
            if (qs) url += '?' + qs;
        }

        // 构建请求头
        const headers = {};
        const token = this.getToken();
        if (token) {
            headers['Authorization'] = 'Bearer ' + token;
        }

        // 构建请求体
        let requestBody = undefined;
        if (body !== undefined && body !== null) {
            if (isForm) {
                // FormData 方式，不设置 Content-Type，让浏览器自动设置 boundary
                requestBody = body;
            } else {
                headers['Content-Type'] = 'application/json';
                requestBody = JSON.stringify(body);
            }
        }

        let response;
        try {
            response = await fetch(url, {
                method,
                headers,
                body: requestBody,
            });
        } catch (err) {
            throw new Error('网络请求失败，请检查网络连接');
        }

        // 处理 401 — token 失效，自动跳转登录
        if (response.status === 401) {
            this.clearToken();
            localStorage.removeItem(CONFIG.USER_KEY);
            if (!location.hash.startsWith('#/login')) {
                UI.toast('登录已过期，请重新登录', 'warning');
                setTimeout(() => Router.navigate('/login'), 800);
            }
            const errBody = await this._safeParseJson(response);
            throw new Error(errBody.detail || '未授权或登录已过期');
        }

        // 处理其他错误状态
        if (!response.ok) {
            const errBody = await this._safeParseJson(response);
            const msg = errBody.detail || errBody.message || `请求失败 (${response.status})`;
            throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }

        // 解析响应
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
            return await response.json();
        }
        if (contentType.includes('text/')) {
            return await response.text();
        }
        // 无内容
        if (response.status === 204) return null;
        return await response.text();
    },

    /** 安全解析 JSON，失败时返回空对象 */
    async _safeParseJson(response) {
        try {
            return await response.json();
        } catch {
            return {};
        }
    },

    /** GET 请求 */
    get(path, params) {
        return this.request('GET', path, { params });
    },

    /** POST 请求 */
    post(path, body, isForm = false) {
        return this.request('POST', path, { body, isForm });
    },

    /** PUT 请求 */
    put(path, body) {
        return this.request('PUT', path, { body });
    },

    /** DELETE 请求 */
    delete(path) {
        return this.request('DELETE', path);
    },
};

/* ============================================================
 * 3. 认证管理
 * ============================================================ */
const Auth = {
    /** 检查是否已登录 */
    isLoggedIn() {
        // 认证关闭时始终视为已登录
        if (!CONFIG.AUTH_ENABLED) return true;
        return !!ApiClient.getToken();
    },

    /** 获取本地缓存的用户信息 */
    getUser() {
        // 认证关闭时返回默认管理员信息
        if (!CONFIG.AUTH_ENABLED) {
            return { username: 'admin', full_name: '系统管理员', role: 'admin' };
        }
        try {
            const raw = localStorage.getItem(CONFIG.USER_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch {
            return null;
        }
    },

    /** 缓存用户信息 */
    setUser(user) {
        if (user) {
            localStorage.setItem(CONFIG.USER_KEY, JSON.stringify(user));
        } else {
            localStorage.removeItem(CONFIG.USER_KEY);
        }
    },

    /** 清除所有认证信息 */
    clear() {
        ApiClient.clearToken();
        localStorage.removeItem(CONFIG.USER_KEY);
    },

    /**
     * 登录
     * @param {string} username
     * @param {string} password
     * @returns {Promise<object>} 用户信息
     */
    async login(username, password) {
        const res = await ApiClient.post('/auth/login', { username, password });
        ApiClient.setToken(res.access_token);
        // 登录后获取用户信息
        const user = await this.fetchMe();
        return user;
    },

    /**
     * 注册
     * @param {object} data - { username, password, email, full_name }
     * @returns {Promise<object>}
     */
    async register(data) {
        return await ApiClient.post('/auth/register', data);
    },

    /** 获取当前用户信息并缓存 */
    async fetchMe() {
        const user = await ApiClient.get('/auth/me');
        this.setUser(user);
        return user;
    },

    /** 登出 */
    async logout() {
        // 认证关闭时无需登出
        if (!CONFIG.AUTH_ENABLED) return;
        this.clear();
        Router.navigate('/login');
    },
};

/* ============================================================
 * 4. UI 通用组件
 * ============================================================ */
const UI = {
    /** 确保 toast 容器存在 */
    _ensureToastContainer() {
        let el = document.getElementById('toast-container');
        if (!el) {
            el = document.createElement('div');
            el.id = 'toast-container';
            el.className = 'toast-container';
            document.body.appendChild(el);
        }
        return el;
    },

    /**
     * 显示 toast 提示
     * @param {string} message - 提示消息
     * @param {string} type - 类型：success/warning/error/info
     * @param {number} duration - 显示时长（毫秒）
     */
    toast(message, type = 'info', duration = 3000) {
        const container = this._ensureToastContainer();
        const icons = { success: '✓', warning: '!', error: '✕', info: 'i' };
        const titles = { success: '成功', warning: '警告', error: '错误', info: '提示' };

        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `
            <span class="toast-icon">${icons[type] || icons.info}</span>
            <div class="toast-content">
                <div class="toast-title">${this.escapeHtml(titles[type] || '提示')}</div>
                <div class="toast-message">${this.escapeHtml(message)}</div>
            </div>
            <button class="toast-close">×</button>
        `;

        const closeBtn = toast.querySelector('.toast-close');
        const removeToast = () => {
            toast.classList.add('hide');
            setTimeout(() => toast.remove(), 300);
        };
        closeBtn.addEventListener('click', removeToast);

        container.appendChild(toast);
        if (duration > 0) {
            setTimeout(removeToast, duration);
        }
    },

    /**
     * 显示模态框
     * @param {string} title - 标题
     * @param {string} contentHtml - 内容 HTML
     * @param {object} options - { size: 'sm'|'md'|'lg'|'xl', footer: '<button>...</button>' }
     * @returns {HTMLElement} 模态框元素
     */
    modal(title, contentHtml, options = {}) {
        // 先关闭已有模态框
        this.closeModal();

        const sizeClass = options.size ? `modal-${options.size}` : '';
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay show';
        overlay.id = 'app-modal';
        overlay.innerHTML = `
            <div class="modal ${sizeClass}">
                <div class="modal-header">
                    <span class="modal-title">${this.escapeHtml(title)}</span>
                    <button class="modal-close" data-action="close-modal">×</button>
                </div>
                <div class="modal-body">${contentHtml}</div>
                ${options.footer ? `<div class="modal-footer">${options.footer}</div>` : ''}
            </div>
        `;

        // 点击遮罩关闭
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target.hasAttribute('data-action')) {
                this.closeModal();
            }
        });

        document.body.appendChild(overlay);
        return overlay;
    },

    /** 关闭模态框 */
    closeModal() {
        const modal = document.getElementById('app-modal');
        if (modal) modal.remove();
    },

    /**
     * 确认对话框
     * @param {string} message - 确认消息
     * @param {object} options - { title, confirmText, cancelText, danger }
     * @returns {Promise<boolean>}
     */
    confirm(message, options = {}) {
        const title = options.title || '确认操作';
        const confirmText = options.confirmText || '确定';
        const cancelText = options.cancelText || '取消';
        const confirmClass = options.danger ? 'btn-danger' : 'btn-primary';

        return new Promise((resolve) => {
            const footer = `
                <button class="btn btn-secondary" data-action="cancel">${this.escapeHtml(cancelText)}</button>
                <button class="btn ${confirmClass}" data-action="confirm">${this.escapeHtml(confirmText)}</button>
            `;
            const modal = this.modal(title, `<p style="font-size:14px;line-height:1.6;">${this.escapeHtml(message)}</p>`, { size: 'sm', footer });

            modal.querySelector('[data-action="cancel"]').addEventListener('click', () => {
                this.closeModal();
                resolve(false);
            });
            modal.querySelector('[data-action="confirm"]').addEventListener('click', () => {
                this.closeModal();
                resolve(true);
            });
        });
    },

    /** 确保 loading 遮罩存在 */
    _ensureLoading() {
        let el = document.getElementById('app-loading');
        if (!el) {
            el = document.createElement('div');
            el.id = 'app-loading';
            el.className = 'loading-fullscreen';
            el.innerHTML = `
                <div class="spinner-container">
                    <div class="spinner spinner-lg"></div>
                    <div class="spinner-text">加载中...</div>
                </div>
            `;
            document.body.appendChild(el);
        }
        return el;
    },

    /**
     * 显示/隐藏全屏加载遮罩
     * @param {boolean} show - 是否显示
     * @param {string} text - 提示文字
     */
    loading(show, text) {
        const el = this._ensureLoading();
        if (show) {
            if (text) {
                const textEl = el.querySelector('.spinner-text');
                if (textEl) textEl.textContent = text;
            }
            el.style.display = 'flex';
        } else {
            el.style.display = 'none';
        }
    },

    /**
     * 格式化日期
     * @param {string|Date} dateStr - 日期字符串或 Date 对象
     * @returns {string} 格式化后的日期，如 "2024-01-15 14:30"
     */
    formatDate(dateStr) {
        if (!dateStr) return '-';
        let date;
        if (dateStr instanceof Date) {
            date = dateStr;
        } else {
            date = new Date(dateStr);
        }
        if (isNaN(date.getTime())) return dateStr;
        const pad = (n) => String(n).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
    },

    /**
     * 根据审核裁决返回徽章 HTML
     * @param {string} verdict - pass / revise / failed
     * @returns {string}
     */
    badge(verdict) {
        const map = {
            pass: { cls: 'badge-success', text: '通过' },
            revise: { cls: 'badge-warning', text: '需修改' },
            reject: { cls: 'badge-danger', text: '拒绝发布' },
            failed: { cls: 'badge-danger', text: '失败' },
        };
        const info = map[verdict] || { cls: 'badge-default', text: verdict || '未知' };
        return `<span class="badge ${info.cls}">${this.escapeHtml(info.text)}</span>`;
    },

    /**
     * 根据严重程度返回徽章 HTML
     * @param {string} severity - critical / major / minor / info
     * @returns {string}
     */
    severityBadge(severity) {
        const map = {
            critical: { cls: 'badge-danger', text: 'CRITICAL' },
            major: { cls: 'badge-warning', text: 'HIGH' },
            minor: { cls: 'badge-info', text: 'MEDIUM' },
            info: { cls: 'badge-default', text: 'LOW' },
        };
        const info = map[severity] || { cls: 'badge-default', text: severity || '未知' };
        return `<span class="badge ${info.cls}">${this.escapeHtml(info.text)}</span>`;
    },

    /**
     * 获取严重程度对应的颜色值（用于卡片左边框）
     */
    _severityColor(severity) {
        const colors = {
            critical: '#dc2626',
            major: '#f59e0b',
            minor: '#3b82f6',
            info: '#94a3b8',
        };
        return colors[severity] || '#94a3b8';
    },

    /**
     * 问题类型中文映射
     * @param {string} type
     * @returns {string}
     */
    issueTypeText(type) {
        const map = {
            inconsistent_with_submission: '与提报表不一致',
            inconsistent_with_website: '与官网不一致',
            unsupported_claim: '无依据宣称',
            exaggeration: '夸大宣传',
            competitor_disparagement: '贬低竞品',
            semantic_risk: '语义风险',
            tone_issue: '语气不当',
        };
        return map[type] || type || '未知';
    },

    /**
     * 生成分页组件 HTML
     * @param {number} total - 总记录数
     * @param {number} page - 当前页
     * @param {number} pageSize - 每页条数
     * @param {function} onPage - 页码回调
     * @returns {string}
     */
    pagination(total, page, pageSize, onPage) {
        const totalPages = Math.max(1, Math.ceil(total / pageSize));
        if (total === 0) return '';

        // 生成页码按钮
        const pages = [];
        const addPage = (p) => pages.push({ p, active: p === page });

        if (totalPages <= 7) {
            for (let i = 1; i <= totalPages; i++) addPage(i);
        } else {
            addPage(1);
            if (page > 4) pages.push({ p: '...', active: false });
            const start = Math.max(2, page - 1);
            const end = Math.min(totalPages - 1, page + 1);
            for (let i = start; i <= end; i++) addPage(i);
            if (page < totalPages - 3) pages.push({ p: '...', active: false });
            addPage(totalPages);
        }

        const pageButtons = pages.map(item => {
            if (item.p === '...') {
                return `<span class="text-secondary p-sm">...</span>`;
            }
            const cls = item.active ? 'btn-primary' : 'btn-secondary';
            return `<button class="btn btn-sm ${cls}" data-page="${item.p}">${item.p}</button>`;
        }).join('');

        const html = `
            <div class="d-flex align-items-center justify-content-between mt-md" style="flex-wrap:wrap;gap:8px;">
                <div class="text-sm text-secondary">
                    共 ${total} 条，第 ${page}/${totalPages} 页
                </div>
                <div class="d-flex gap-xs">
                    <button class="btn btn-sm btn-secondary" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>上一页</button>
                    ${pageButtons}
                    <button class="btn btn-sm btn-secondary" data-page="${page + 1}" ${page >= totalPages ? 'disabled' : ''}>下一页</button>
                </div>
            </div>
        `;

        // 绑定事件（延迟执行，等 DOM 更新后）
        setTimeout(() => {
            document.querySelectorAll('[data-page]').forEach(btn => {
                btn.onclick = null;
                btn.addEventListener('click', (e) => {
                    const p = parseInt(e.currentTarget.dataset.page);
                    if (!isNaN(p) && p >= 1 && p <= totalPages && typeof onPage === 'function') {
                        onPage(p);
                    }
                });
            });
        }, 0);

        return html;
    },

    /**
     * JSON 格式化显示
     * @param {any} data - 任意 JSON 数据
     * @returns {string} HTML
     */
    jsonViewer(data) {
        const jsonStr = JSON.stringify(data, null, 2);
        // 语法高亮
        const highlighted = jsonStr
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/("(?:\\.|[^"\\])*")(\s*:)/g, '<span class="json-key">$1</span>$2')
            .replace(/:\s*("(?:\\.|[^"\\])*")/g, ': <span class="json-string">$1</span>')
            .replace(/:\s*(-?\d+\.?\d*)/g, ': <span class="json-number">$1</span>')
            .replace(/:\s*(true|false)/g, ': <span class="json-boolean">$1</span>')
            .replace(/:\s*null/g, ': <span class="json-null">null</span>');
        return `<div class="json-viewer"><pre style="margin:0;white-space:pre-wrap;">${highlighted}</pre></div>`;
    },

    /**
     * HTML 转义
     * @param {string} text
     * @returns {string}
     */
    escapeHtml(text) {
        if (text === null || text === undefined) return '';
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },

    /** 空状态 HTML */
    emptyState(message, icon = '📭') {
        return `
            <div class="empty-state">
                <div class="empty-icon">${icon}</div>
                <div>${this.escapeHtml(message || '暂无数据')}</div>
            </div>
        `;
    },

    /** 骨架屏 HTML */
    skeleton(rows = 5) {
        const items = Array(rows).fill(0).map(() =>
            `<div class="skeleton skeleton-text" style="height:14px;"></div>`
        ).join('');
        return `<div style="padding:16px;">${items}</div>`;
    },

    /**
     * 防抖函数 — 用于搜索/输入场景，避免每次按键都发请求
     * @param {Function} fn - 要执行的函数
     * @param {number} delay - 延迟毫秒数
     * @returns {Function}
     */
    debounce(fn, delay = 300) {
        let timer = null;
        return function (...args) {
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    },
};

/* ============================================================
 * 5. 页面渲染器
 * ============================================================ */
const Pages = {

    /* --------------------------------------------------------
     * 登录 / 注册页面
     * -------------------------------------------------------- */
    renderLogin() {
        return `
            <div class="login-page">
                <div class="login-card">
                    <div class="login-header">
                        <div class="login-logo">📝</div>
                        <h1 class="login-title">GEO 生文审核系统</h1>
                        <p class="login-subtitle">智能内容审核平台</p>
                    </div>

                    <div class="btn-group w-100 mb-lg">
                        <button class="btn btn-secondary auth-tab active" data-tab="login" style="flex:1;">登录</button>
                        <button class="btn btn-secondary auth-tab" data-tab="register" style="flex:1;">注册</button>
                    </div>

                    <!-- 登录表单 -->
                    <form id="login-form" class="login-form">
                        <div class="form-group">
                            <label class="form-label">用户名</label>
                            <input type="text" id="login-username" placeholder="请输入用户名" required autocomplete="username">
                        </div>
                        <div class="form-group">
                            <label class="form-label">密码</label>
                            <input type="password" id="login-password" placeholder="请输入密码" required autocomplete="current-password">
                        </div>
                        <button type="submit" class="btn btn-primary btn-block btn-lg">登 录</button>
                    </form>

                    <!-- 注册表单 -->
                    <form id="register-form" class="login-form" style="display:none;">
                        <div class="form-group">
                            <label class="form-label">用户名 <span class="required">*</span></label>
                            <input type="text" id="reg-username" placeholder="至少 3 个字符" required minlength="3">
                        </div>
                        <div class="form-group">
                            <label class="form-label">密码 <span class="required">*</span></label>
                            <input type="password" id="reg-password" placeholder="至少 6 个字符" required minlength="6" autocomplete="new-password">
                        </div>
                        <div class="form-group">
                            <label class="form-label">邮箱</label>
                            <input type="email" id="reg-email" placeholder="可选" autocomplete="email">
                        </div>
                        <div class="form-group">
                            <label class="form-label">姓名</label>
                            <input type="text" id="reg-fullname" placeholder="可选">
                        </div>
                        <button type="submit" class="btn btn-primary btn-block btn-lg">注 册</button>
                    </form>

                    <div class="login-footer">
                        <p>© 2024 GEO 生文审核系统</p>
                    </div>
                </div>
            </div>
        `;
    },

    /** 绑定登录页事件 */
    bindLoginEvents() {
        // 切换登录/注册标签
        document.querySelectorAll('.auth-tab').forEach(tab => {
            tab.addEventListener('click', (e) => {
                const tabName = e.currentTarget.dataset.tab;
                document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
                e.currentTarget.classList.add('active');

                document.getElementById('login-form').style.display = tabName === 'login' ? 'block' : 'none';
                document.getElementById('register-form').style.display = tabName === 'register' ? 'block' : 'none';
            });
        });

        // 登录表单提交
        const loginForm = document.getElementById('login-form');
        if (loginForm) {
            loginForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                const username = document.getElementById('login-username').value.trim();
                const password = document.getElementById('login-password').value;

                if (!username || !password) {
                    UI.toast('请输入用户名和密码', 'warning');
                    return;
                }

                const submitBtn = loginForm.querySelector('button[type="submit"]');
                const originalText = submitBtn.textContent;
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<div class="spinner"></div>';

                try {
                    await Auth.login(username, password);
                    UI.toast('登录成功', 'success');
                    Router.navigate('/dashboard');
                } catch (err) {
                    UI.toast(err.message || '登录失败', 'error');
                } finally {
                    submitBtn.disabled = false;
                    submitBtn.textContent = originalText;
                }
            });
        }

        // 注册表单提交
        const regForm = document.getElementById('register-form');
        if (regForm) {
            regForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                const data = {
                    username: document.getElementById('reg-username').value.trim(),
                    password: document.getElementById('reg-password').value,
                    email: document.getElementById('reg-email').value.trim() || undefined,
                    full_name: document.getElementById('reg-fullname').value.trim() || undefined,
                };

                if (!data.username || !data.password) {
                    UI.toast('请填写用户名和密码', 'warning');
                    return;
                }
                if (data.username.length < 3) {
                    UI.toast('用户名至少 3 个字符', 'warning');
                    return;
                }
                if (data.password.length < 6) {
                    UI.toast('密码至少 6 个字符', 'warning');
                    return;
                }

                const submitBtn = regForm.querySelector('button[type="submit"]');
                const originalText = submitBtn.textContent;
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<div class="spinner"></div>';

                try {
                    await Auth.register(data);
                    UI.toast('注册成功，请登录', 'success');
                    // 切换到登录标签
                    document.querySelector('.auth-tab[data-tab="login"]').click();
                    document.getElementById('login-username').value = data.username;
                } catch (err) {
                    UI.toast(err.message || '注册失败', 'error');
                } finally {
                    submitBtn.disabled = false;
                    submitBtn.textContent = originalText;
                }
            });
        }
    },

    /* --------------------------------------------------------
     * 仪表盘
     * -------------------------------------------------------- */
    async renderDashboard() {
        App.updateContent(UI.skeleton(4));

        try {
            const [stats, recentRes] = await Promise.all([
                ApiClient.get('/history/statistics').catch(() => ({})),
                ApiClient.get('/history', { page: 1, page_size: 5 }).catch(() => ({ data: [], total: 0 })),
            ]);

            const recent = recentRes.data || [];
            const total = stats.total_reviews || 0;
            const passCount = stats.pass_count || 0;
            const reviseCount = stats.revise_count || 0;
            const passRate = total > 0 ? ((passCount / total) * 100).toFixed(1) : '0.0';
            const avgIssuesRaw = stats.avg_issues_per_review ?? stats.avg_issues ?? 0;
            const avgIssues = avgIssuesRaw.toFixed(1);

            const html = `
                <!-- 欢迎横幅 -->
                <div class="welcome-banner mb-lg">
                    <div class="welcome-banner-content">
                        <div class="welcome-banner-left">
                            <h1 class="welcome-title">GEO 智能审核平台</h1>
                            <p class="welcome-desc">基于大模型的AI内容合规审核系统，支持多行业动态规则、GEO可引用性分析、品牌一致性校验</p>
                            <div class="welcome-tags">
                                <span class="welcome-tag">🤖 LLM语义审核</span>
                                <span class="welcome-tag">📋 规则引擎</span>
                                <span class="welcome-tag">🔍 GEO可引用性</span>
                                <span class="welcome-tag">🏷️ 品牌一致性</span>
                            </div>
                        </div>
                        <div class="welcome-banner-right">
                            <div class="welcome-stat-ring">
                                <svg viewBox="0 0 120 120" class="stat-ring-svg">
                                    <circle cx="60" cy="60" r="52" fill="none" stroke="rgba(255,255,255,0.15)" stroke-width="8"/>
                                    <circle cx="60" cy="60" r="52" fill="none" stroke="#fff" stroke-width="8"
                                        stroke-dasharray="${(passRate / 100) * 327} 327"
                                        stroke-linecap="round" transform="rotate(-90 60 60)"
                                        class="stat-ring-progress"/>
                                </svg>
                                <div class="stat-ring-text">
                                    <span class="stat-ring-value">${passRate}</span>
                                    <span class="stat-ring-unit">%</span>
                                </div>
                            </div>
                            <div class="text-sm" style="color:rgba(255,255,255,0.8);">总体通过率</div>
                        </div>
                    </div>
                </div>

                <!-- 统计卡片 -->
                <div class="stat-cards-row mb-lg">
                    <div class="stat-card stat-card-primary">
                        <div class="stat-card-icon">📝</div>
                        <div class="stat-card-info">
                            <div class="stat-card-value" id="stat-total">${total}</div>
                            <div class="stat-card-label">总审核数</div>
                        </div>
                    </div>
                    <div class="stat-card stat-card-success">
                        <div class="stat-card-icon">✅</div>
                        <div class="stat-card-info">
                            <div class="stat-card-value">${passCount}</div>
                            <div class="stat-card-label">通过</div>
                        </div>
                    </div>
                    <div class="stat-card stat-card-warning">
                        <div class="stat-card-icon">⚠️</div>
                        <div class="stat-card-info">
                            <div class="stat-card-value">${reviseCount}</div>
                            <div class="stat-card-label">需修改</div>
                        </div>
                    </div>
                    <div class="stat-card stat-card-danger">
                        <div class="stat-card-icon">🔴</div>
                        <div class="stat-card-info">
                            <div class="stat-card-value">${stats.critical_count || 0}</div>
                            <div class="stat-card-label">严重问题</div>
                        </div>
                    </div>
                    <div class="stat-card stat-card-info">
                        <div class="stat-card-icon">📊</div>
                        <div class="stat-card-info">
                            <div class="stat-card-value">${avgIssues}</div>
                            <div class="stat-card-label">平均问题数</div>
                        </div>
                    </div>
                </div>

                <!-- 双栏布局：问题分布 + 快捷入口 -->
                <div class="dashboard-grid mb-lg">
                    <!-- 审核结论分布 + 问题严重程度 -->
                    ${(() => {
                        const bySev = stats.by_severity || {};
                        const cCount = bySev.critical || 0;
                        const mCount = bySev.major || 0;
                        const miCount = bySev.minor || 0;
                        const iCount = bySev.info || 0;
                        const totalIssues = cCount + mCount + miCount + iCount;
                        const bars = [
                            { label: 'CRITICAL', count: cCount, color: '#dc2626', icon: '🔴' },
                            { label: 'HIGH', count: mCount, color: '#f59e0b', icon: '🟠' },
                            { label: 'MEDIUM', count: miCount, color: '#3b82f6', icon: '🟡' },
                            { label: 'LOW', count: iCount, color: '#94a3b8', icon: '🔵' },
                        ];
                        const maxCount = Math.max(...bars.map(b => b.count), 1);

                        // 审核结论分布
                        const byVerdict = stats.by_verdict || {};
                        const vPass = byVerdict.pass || stats.pass_count || 0;
                        const vRevise = byVerdict.revise || stats.revise_count || 0;
                        const vReject = byVerdict.reject || 0;
                        const vTotal = vPass + vRevise + vReject || 1;

                        // SVG donut chart
                        const r = 42, cx = 60, cy = 60, circumference = 2 * Math.PI * r;
                        const passPct = vPass / vTotal;
                        const revisePct = vRevise / vTotal;
                        const rejectPct = vReject / vTotal;
                        const passDash = passPct * circumference;
                        const reviseDash = revisePct * circumference;
                        const rejectDash = rejectPct * circumference;

                        return `
                            <div class="card">
                                <div class="card-header">
                                    <span class="card-title">审核分布</span>
                                    <span class="text-sm text-secondary">共 ${totalIssues} 个问题</span>
                                </div>
                                <div class="card-body">
                                    <!-- 审核结论环形图 -->
                                    <div style="display:flex;gap:24px;align-items:center;margin-bottom:20px;">
                                        <svg viewBox="0 0 120 120" style="width:120px;height:120px;flex-shrink:0;">
                                            <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#f0f0f0" stroke-width="14"/>
                                            ${vPass > 0 ? `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#22c55e" stroke-width="14"
                                                stroke-dasharray="${passDash} ${circumference - passDash}" stroke-dashoffset="0" transform="rotate(-90 ${cx} ${cy})"/>` : ''}
                                            ${vRevise > 0 ? `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#f59e0b" stroke-width="14"
                                                stroke-dasharray="${reviseDash} ${circumference - reviseDash}" stroke-dashoffset="${-passDash}" transform="rotate(-90 ${cx} ${cy})"/>` : ''}
                                            ${vReject > 0 ? `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#ef4444" stroke-width="14"
                                                stroke-dasharray="${rejectDash} ${circumference - rejectDash}" stroke-dashoffset="${-(passDash + reviseDash)}" transform="rotate(-90 ${cx} ${cy})"/>` : ''}
                                            <text x="${cx}" y="${cy-4}" text-anchor="middle" style="font-size:18px;font-weight:600;fill:#2c3e50;">${vTotal}</text>
                                            <text x="${cx}" y="${cy+14}" text-anchor="middle" style="font-size:10px;fill:#94a3b8;">总数</text>
                                        </svg>
                                        <div style="display:flex;flex-direction:column;gap:8px;">
                                            <div style="display:flex;align-items:center;gap:8px;">
                                                <span style="width:10px;height:10px;border-radius:50%;background:#22c55e;display:inline-block;"></span>
                                                <span style="font-size:13px;color:#2c3e50;">通过 <b>${vPass}</b> (${(passPct*100).toFixed(0)}%)</span>
                                            </div>
                                            <div style="display:flex;align-items:center;gap:8px;">
                                                <span style="width:10px;height:10px;border-radius:50%;background:#f59e0b;display:inline-block;"></span>
                                                <span style="font-size:13px;color:#2c3e50;">需修改 <b>${vRevise}</b> (${(revisePct*100).toFixed(0)}%)</span>
                                            </div>
                                            <div style="display:flex;align-items:center;gap:8px;">
                                                <span style="width:10px;height:10px;border-radius:50%;background:#ef4444;display:inline-block;"></span>
                                                <span style="font-size:13px;color:#2c3e50;">驳回 <b>${vReject}</b> (${(rejectPct*100).toFixed(0)}%)</span>
                                            </div>
                                        </div>
                                    </div>
                                    <!-- 问题严重程度柱状图 -->
                                    <div style="border-top:1px solid var(--color-border);padding-top:16px;">
                                        <div class="text-sm text-secondary mb-sm">问题严重程度</div>
                                        ${totalIssues === 0 ? '<div class="text-sm text-secondary" style="padding:16px 0;text-align:center;">暂无问题数据</div>' : `
                                            <div class="severity-chart">
                                                ${bars.map(bar => {
                                                    const heightPct = (bar.count / maxCount) * 100;
                                                    return `
                                                        <div class="severity-bar-col">
                                                            <div class="severity-bar-count">${bar.count}</div>
                                                            <div class="severity-bar-track">
                                                                <div class="severity-bar-fill severity-bar-fill-${bar.label.toLowerCase()}"
                                                                    style="height:${heightPct}%;background:${bar.color};">
                                                                </div>
                                                            </div>
                                                            <div class="severity-bar-label">${bar.icon} ${bar.label}</div>
                                                        </div>
                                                    `;
                                                }).join('')}
                                            </div>
                                        `}
                                    </div>
                                </div>
                            </div>
                        `;
                    })()}

                    <!-- 快捷入口 -->
                    <div class="card">
                        <div class="card-header">
                            <span class="card-title">快速操作</span>
                        </div>
                        <div class="card-body">
                            <div class="quick-actions-grid">
                                <a href="#/review" class="quick-action-card">
                                    <div class="quick-action-icon qa-review">✏️</div>
                                    <div class="quick-action-text">
                                        <div class="text-bold">提交审核</div>
                                        <div class="text-sm text-secondary">文本/文件/链接三种方式</div>
                                    </div>
                                </a>
                                <a href="#/batch" class="quick-action-card">
                                    <div class="quick-action-icon qa-batch">📦</div>
                                    <div class="quick-action-text">
                                        <div class="text-bold">批量审核</div>
                                        <div class="text-sm text-secondary">并发处理大量任务</div>
                                    </div>
                                </a>
                                <a href="#/rules" class="quick-action-card">
                                    <div class="quick-action-icon qa-rules">🛡️</div>
                                    <div class="quick-action-text">
                                        <div class="text-bold">规则管理</div>
                                        <div class="text-sm text-secondary">查看、测试、编辑规则</div>
                                    </div>
                                </a>
                                <a href="#/history" class="quick-action-card">
                                    <div class="quick-action-icon qa-history">📋</div>
                                    <div class="quick-action-text">
                                        <div class="text-bold">审核历史</div>
                                        <div class="text-sm text-secondary">搜索和回溯历史记录</div>
                                    </div>
                                </a>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 最近审核记录 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">最近审核记录</span>
                        <a href="#/history" class="btn btn-link btn-sm">查看全部 →</a>
                    </div>
                    <div class="card-body p-0">
                        ${recent.length === 0 ? UI.emptyState('暂无审核记录', '📋') : `
                            <div class="table-wrapper" style="border:none;border-radius:0;">
                                <table class="table-hover">
                                    <thead>
                                        <tr>
                                            <th>任务名称</th>
                                            <th>公司</th>
                                            <th>结论</th>
                                            <th>问题数</th>
                                            <th>审核时间</th>
                                            <th>操作</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${recent.map(r => `
                                            <tr data-id="${r.review_id || r.id}">
                                                <td>${UI.escapeHtml(r.task_name || '-')}</td>
                                                <td>${UI.escapeHtml(r.company_name || '-')}</td>
                                                <td>${UI.badge(r.verdict)}</td>
                                                <td>${r.total_issues || 0}</td>
                                                <td class="text-sm text-secondary">${UI.formatDate(r.reviewed_at)}</td>
                                                <td>
                                                    <button class="btn btn-link btn-sm" data-action="view-history" data-id="${r.review_id || r.id}">详情</button>
                                                </td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            </div>
                        `}
                    </div>
                </div>
            `;

            App.updateContent(html);

            // 绑定查看详情事件
            document.querySelectorAll('[data-action="view-history"]').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const id = e.currentTarget.dataset.id;
                    Router.navigate(`/history/detail?id=${id}`);
                });
            });

            // 表格行点击
            document.querySelectorAll('tr[data-id]').forEach(tr => {
                tr.addEventListener('click', () => {
                    const id = tr.dataset.id;
                    Router.navigate(`/history/detail?id=${id}`);
                });
            });
        } catch (err) {
            App.updateContent(`
                <div class="empty-state">
                    <div class="empty-icon">⚠️</div>
                    <div class="mb-md">加载失败：${UI.escapeHtml(err.message)}</div>
                    <button class="btn btn-primary" onclick="location.reload()">重新加载</button>
                </div>
            `);
        }
    },

    /* --------------------------------------------------------
     * 审核提交页面
     * -------------------------------------------------------- */
    async renderReview() {
        // 获取规则模板列表
        let templates = [];
        try {
            const res = await ApiClient.get('/rules/templates');
            templates = res.templates || [];
        } catch {
            // 忽略错误
        }

        const html = `
            <div class="d-flex align-items-center justify-content-between mb-lg">
                <h2 style="font-size:20px;font-weight:600;">提交审核</h2>
            </div>

            <div class="card">
                <div class="card-header">
                    <span class="card-title">审核模式</span>
                </div>
                <div class="card-body">
                    <div class="btn-group mb-lg">
                        <button class="btn btn-secondary review-mode-tab active" data-mode="json" style="min-width:120px;">文本输入</button>
                        <button class="btn btn-secondary review-mode-tab" data-mode="upload" style="min-width:120px;">文件上传</button>
                        <button class="btn btn-secondary review-mode-tab" data-mode="link" style="min-width:120px;">链接导入</button>
                    </div>

                    <!-- 文本输入模式 -->
                    <div id="review-json-mode">
                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label">公司名称（可选）</label>
                                <input type="text" id="review-text-company" placeholder="请输入公司名称">
                            </div>
                            <div class="form-group">
                                <label class="form-label">任务名称（可选）</label>
                                <input type="text" id="review-text-task" placeholder="请输入任务名称">
                            </div>
                        </div>

                        <div class="form-group">
                            <label class="form-label">提报表文件 <span class="required">*</span></label>
                            <div style="display:flex;gap:8px;align-items:center;">
                                <input type="file" id="review-text-submission-file" accept=".xlsx,.xls,.json,.txt" style="display:none;">
                                <button class="btn btn-secondary btn-sm" id="choose-review-submission-btn">选择提报表</button>
                                <span id="review-submission-file-name" class="text-secondary text-sm">未选择</span>
                                <button class="btn btn-text btn-sm" id="clear-review-submission-btn" style="display:none;color:#dc2626;">清除</button>
                            </div>
                            <div class="form-hint">支持 xlsx/xls/json/txt 格式，上传后将解析提报表内容进行审核</div>
                        </div>

                        <div class="form-group">
                            <label class="form-label">待审核正文 <span class="required">*</span></label>
                            <textarea id="review-text-input" rows="12" placeholder="请直接复制粘贴待审核的文本内容..."></textarea>
                            <div class="d-flex justify-content-between mt-xs">
                                <span class="form-hint">直接粘贴或输入需要审核的文本内容</span>
                                <span class="text-sm text-secondary" id="text-char-count">0 字</span>
                            </div>
                        </div>

                        <div class="form-group">
                            <label class="form-label">官网 URL（可选）</label>
                            <input type="text" id="review-json-official-urls" placeholder="多个 URL 用英文逗号分隔，如 https://a.com,https://b.com">
                        </div>

                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label">规则模板</label>
                                <select id="review-json-template">
                                    <option value="">默认规则</option>
                                    ${templates.map(t => `<option value="${UI.escapeHtml(t)}">${UI.escapeHtml(t)}</option>`).join('')}
                                </select>
                            </div>
                            <div class="form-group">
                                <label class="form-label">行业类型</label>
                                <select id="review-json-industry">
                                    <option value="">自动识别</option>
                                    <option value="finance">金融/理财</option>
                                    <option value="medical">医疗/健康</option>
                                    <option value="enterprise_intro">企业介绍</option>
                                    <option value="news">新闻稿</option>
                                    <option value="technology">科技/技术</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label class="form-label">输出格式</label>
                                <select id="review-json-format">
                                    <option value="text" selected>文本</option>
                                    <option value="json">JSON</option>
                                    <option value="markdown">Markdown</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label class="form-label">是否爬取官网</label>
                                <select id="review-json-crawl">
                                    <option value="true">是</option>
                                    <option value="false">否</option>
                                </select>
                            </div>
                        </div>

                        <button class="btn btn-primary" id="review-json-submit">
                            <span>开始审核</span>
                        </button>
                    </div>

                    <!-- 文件上传模式 -->
                    <div id="review-upload-mode" style="display:none;">
                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label">待审正文文件 <span class="required">*</span></label>
                                <input type="file" id="content-file" accept=".pdf,.docx,.doc,.txt">
                                <div class="form-hint">支持 PDF / DOCX / DOC / TXT 格式</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">提报表文件 <span class="required">*</span></label>
                                <input type="file" id="submission-file" accept=".xlsx,.xls,.docx,.doc,.pdf,.txt">
                                <div class="form-hint">支持 XLSX / XLS / DOCX / DOC / PDF / TXT 格式</div>
                            </div>
                        </div>

                        <div class="form-group">
                            <label class="form-label">官网 URL（可选）</label>
                            <input type="text" id="official-urls" placeholder="多个 URL 用英文逗号分隔，如 https://a.com,https://b.com">
                        </div>

                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label">规则模板</label>
                                <select id="review-upload-template">
                                    <option value="">默认规则</option>
                                    ${templates.map(t => `<option value="${UI.escapeHtml(t)}">${UI.escapeHtml(t)}</option>`).join('')}
                                </select>
                            </div>
                            <div class="form-group">
                                <label class="form-label">行业类型</label>
                                <select id="review-upload-industry">
                                    <option value="">自动识别</option>
                                    <option value="finance">金融/理财</option>
                                    <option value="medical">医疗/健康</option>
                                    <option value="enterprise_intro">企业介绍</option>
                                    <option value="news">新闻稿</option>
                                    <option value="technology">科技/技术</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label class="form-label">是否爬取官网</label>
                                <select id="crawl-urls">
                                    <option value="true">是</option>
                                    <option value="false">否</option>
                                </select>
                            </div>
                        </div>

                        <button class="btn btn-primary" id="review-upload-submit">
                            <span>开始审核</span>
                        </button>
                    </div>

                    <!-- 链接导入模式 -->
                    <div id="review-link-mode" style="display:none;">
                        <div class="form-group">
                            <label class="form-label">文档链接 <span class="required">*</span></label>
                            <input type="text" id="document-url" placeholder="粘贴飞书文档链接或其他网页链接，如 https://xxx.feishu.cn/docx/...">
                            <div class="form-hint">支持飞书文档链接（feishu.cn）和通用网页链接，系统将自动抓取文档内容</div>
                        </div>

                        <div class="form-group">
                            <label class="form-label">提报表文件 <span class="required">*</span></label>
                            <div style="display:flex;gap:8px;align-items:center;">
                                <input type="file" id="review-link-submission-file" accept=".xlsx,.xls,.json,.txt" style="display:none;">
                                <button class="btn btn-secondary btn-sm" id="choose-link-submission-btn">选择提报表</button>
                                <span id="link-submission-file-name" class="text-secondary text-sm">未选择</span>
                                <button class="btn btn-text btn-sm" id="clear-link-submission-btn" style="display:none;color:#dc2626;">清除</button>
                            </div>
                            <div class="form-hint">支持 xlsx/xls/json/txt 格式</div>
                        </div>

                        <div class="form-group">
                            <label class="form-label">官网 URL（可选）</label>
                            <input type="text" id="review-link-official-urls" placeholder="多个 URL 用英文逗号分隔，如 https://a.com,https://b.com">
                        </div>

                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label">规则模板</label>
                                <select id="review-link-template">
                                    <option value="">默认规则</option>
                                    ${templates.map(t => `<option value="${UI.escapeHtml(t)}">${UI.escapeHtml(t)}</option>`).join('')}
                                </select>
                            </div>
                            <div class="form-group">
                                <label class="form-label">行业类型</label>
                                <select id="review-link-industry">
                                    <option value="">自动识别</option>
                                    <option value="finance">金融/理财</option>
                                    <option value="medical">医疗/健康</option>
                                    <option value="enterprise_intro">企业介绍</option>
                                    <option value="news">新闻稿</option>
                                    <option value="technology">科技/技术</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label class="form-label">是否爬取官网</label>
                                <select id="review-link-crawl">
                                    <option value="true">是</option>
                                    <option value="false">否</option>
                                </select>
                            </div>
                        </div>

                        <button class="btn btn-primary" id="review-link-submit">
                            <span>开始审核</span>
                        </button>
                    </div>
                </div>
            </div>

            <!-- 审核结果区域 -->
            <div id="review-result-area"></div>
        `;

        App.updateContent(html);
        this.bindReviewEvents();
    },

    /** 绑定审核页面事件 */
    bindReviewEvents() {
        // 切换模式
        document.querySelectorAll('.review-mode-tab').forEach(tab => {
            tab.addEventListener('click', (e) => {
                const mode = e.currentTarget.dataset.mode;
                document.querySelectorAll('.review-mode-tab').forEach(t => t.classList.remove('active'));
                e.currentTarget.classList.add('active');
                document.getElementById('review-json-mode').style.display = mode === 'json' ? 'block' : 'none';
                document.getElementById('review-upload-mode').style.display = mode === 'upload' ? 'block' : 'none';
                document.getElementById('review-link-mode').style.display = mode === 'link' ? 'block' : 'none';
            });
        });

        // 实时字数统计
        const textInput = document.getElementById('review-text-input');
        const charCount = document.getElementById('text-char-count');
        if (textInput && charCount) {
            const updateCount = () => {
                const len = textInput.value.length;
                charCount.textContent = `${len} 字`;
                // 字数过少/过多时变色提示
                if (len > 0 && len < 50) {
                    charCount.style.color = '#f59e0b';
                } else if (len > 5000) {
                    charCount.style.color = '#dc2626';
                } else {
                    charCount.style.color = '';
                }
            };
            textInput.addEventListener('input', updateCount);
        }

        // 提报表文件选择（文本输入模式）
        const chooseReviewSubBtn = document.getElementById('choose-review-submission-btn');
        const reviewSubFileInput = document.getElementById('review-text-submission-file');
        if (chooseReviewSubBtn && reviewSubFileInput) {
            chooseReviewSubBtn.addEventListener('click', () => reviewSubFileInput.click());
            reviewSubFileInput.addEventListener('change', function() {
                if (this.files && this.files.length > 0) {
                    App._reviewSubmissionFile = this.files[0];
                    document.getElementById('review-submission-file-name').textContent = this.files[0].name;
                    document.getElementById('clear-review-submission-btn').style.display = 'inline-block';
                }
            });
        }
        const clearReviewSubBtn = document.getElementById('clear-review-submission-btn');
        if (clearReviewSubBtn) {
            clearReviewSubBtn.addEventListener('click', () => {
                App._reviewSubmissionFile = null;
                document.getElementById('review-text-submission-file').value = '';
                document.getElementById('review-submission-file-name').textContent = '未选择';
                clearReviewSubBtn.style.display = 'none';
            });
        }

        // JSON 方式提交（支持提报表文件上传）
        const jsonSubmit = document.getElementById('review-json-submit');
        if (jsonSubmit) {
            jsonSubmit.addEventListener('click', async () => {
                const contentText = document.getElementById('review-text-input').value.trim();
                if (!contentText) {
                    UI.toast('请输入待审核正文', 'warning');
                    return;
                }

                const submissionFile = App._reviewSubmissionFile;
                if (!submissionFile) {
                    UI.toast('请选择提报表文件', 'warning');
                    return;
                }

                const companyName = document.getElementById('review-text-company').value.trim();
                const taskName = document.getElementById('review-text-task').value.trim();

                const btn = jsonSubmit;
                btn.disabled = true;
                btn.innerHTML = '<div class="spinner"></div> 审核中...';
                this._startReviewProgress();

                try {
                    const formData = new FormData();
                    // 将文本内容包装为文件对象，使用现有 /review/upload 端点
                    const textBlob = new Blob([contentText], { type: 'text/plain' });
                    const textFile = new File([textBlob], 'content.txt', { type: 'text/plain' });
                    formData.append('content_file', textFile);
                    formData.append('submission_file', submissionFile);

                    const template = document.getElementById('review-json-template').value;
                    if (template) formData.append('rule_template', template);

                    const officialUrls = document.getElementById('review-json-official-urls').value.trim();
                    if (officialUrls) formData.append('official_urls', officialUrls);

                    formData.append('crawl_official_urls', document.getElementById('review-json-crawl').value);
                    formData.append('output_format', 'json');

                    const industry = document.getElementById('review-json-industry').value;
                    if (industry) formData.append('industry', industry);

                    const result = await ApiClient.post('/review/upload', formData, true);
                    App.lastReviewResult = result;
                    UI.toast('审核完成', 'success');
                    this.renderReviewResult(result);
                } catch (err) {
                    UI.toast(err.message || '审核失败', 'error');
                } finally {
                    btn.disabled = false;
                    btn.innerHTML = '<span>开始审核</span>';
                    this._stopReviewProgress();
                }
            });
        }

        // 文件上传方式提交
        const uploadSubmit = document.getElementById('review-upload-submit');
        if (uploadSubmit) {
            uploadSubmit.addEventListener('click', async () => {
                const contentFile = document.getElementById('content-file').files[0];
                const submissionFile = document.getElementById('submission-file').files[0];

                if (!contentFile || !submissionFile) {
                    UI.toast('请选择待审正文文件和提报表文件', 'warning');
                    return;
                }

                const formData = new FormData();
                formData.append('content_file', contentFile);
                formData.append('submission_file', submissionFile);

                const officialUrls = document.getElementById('official-urls').value.trim();
                if (officialUrls) formData.append('official_urls', officialUrls);

                const template = document.getElementById('review-upload-template').value;
                if (template) formData.append('rule_template', template);

                formData.append('output_format', 'json');
                formData.append('crawl_official_urls', document.getElementById('crawl-urls').value);

                const industry = document.getElementById('review-upload-industry').value;
                if (industry) formData.append('industry', industry);

                const btn = uploadSubmit;
                btn.disabled = true;
                btn.innerHTML = '<div class="spinner"></div> 审核中...';
                this._startReviewProgress();

                try {
                    const result = await ApiClient.post('/review/upload', formData, true);
                    App.lastReviewResult = result;
                    UI.toast('审核完成', 'success');
                    this.renderReviewResult(result);
                } catch (err) {
                    UI.toast(err.message || '审核失败', 'error');
                } finally {
                    btn.disabled = false;
                    btn.innerHTML = '<span>开始审核</span>';
                    this._stopReviewProgress();
                }
            });
        }

        // 提报表文件选择（链接导入模式）
        const chooseLinkSubBtn = document.getElementById('choose-link-submission-btn');
        const linkSubFileInput = document.getElementById('review-link-submission-file');
        if (chooseLinkSubBtn && linkSubFileInput) {
            chooseLinkSubBtn.addEventListener('click', () => linkSubFileInput.click());
            linkSubFileInput.addEventListener('change', function() {
                if (this.files && this.files.length > 0) {
                    App._linkSubmissionFile = this.files[0];
                    document.getElementById('link-submission-file-name').textContent = this.files[0].name;
                    document.getElementById('clear-link-submission-btn').style.display = 'inline-block';
                }
            });
        }
        const clearLinkSubBtn = document.getElementById('clear-link-submission-btn');
        if (clearLinkSubBtn) {
            clearLinkSubBtn.addEventListener('click', () => {
                App._linkSubmissionFile = null;
                document.getElementById('review-link-submission-file').value = '';
                document.getElementById('link-submission-file-name').textContent = '未选择';
                clearLinkSubBtn.style.display = 'none';
            });
        }

        // 链接导入方式提交
        const linkSubmit = document.getElementById('review-link-submit');
        if (linkSubmit) {
            linkSubmit.addEventListener('click', async () => {
                const documentUrl = document.getElementById('document-url').value.trim();
                if (!documentUrl) {
                    UI.toast('请输入文档链接', 'warning');
                    return;
                }

                const submissionFile = App._linkSubmissionFile;
                if (!submissionFile) {
                    UI.toast('请选择提报表文件', 'warning');
                    return;
                }

                const formData = new FormData();
                formData.append('document_url', documentUrl);
                formData.append('submission_file', submissionFile);

                const officialUrls = document.getElementById('review-link-official-urls').value.trim();
                if (officialUrls) formData.append('official_urls', officialUrls);

                const template = document.getElementById('review-link-template').value;
                if (template) formData.append('rule_template', template);

                formData.append('output_format', 'json');
                formData.append('crawl_official_urls', document.getElementById('review-link-crawl').value);

                const industry = document.getElementById('review-link-industry').value;
                if (industry) formData.append('industry', industry);

                const btn = linkSubmit;
                btn.disabled = true;
                btn.innerHTML = '<div class="spinner"></div> 抓取并审核中...';
                this._startReviewProgress();

                try {
                    const result = await ApiClient.post('/review/upload', formData, true);
                    App.lastReviewResult = result;
                    UI.toast('审核完成', 'success');
                    this.renderReviewResult(result);
                } catch (err) {
                    UI.toast(err.message || '审核失败', 'error');
                } finally {
                    btn.disabled = false;
                    btn.innerHTML = '<span>开始审核</span>';
                    this._stopReviewProgress();
                }
            });
        }
    },

    /**
     * 显示审核进度步骤提示
     */
    _reviewProgressTimer: null,
    _reviewStartTime: null,
    _startReviewProgress() {
        const area = document.getElementById('review-result-area');
        if (!area) return;

        this._reviewStartTime = Date.now();

        const steps = [
            { icon: '📋', label: '解析提报表' },
            { icon: '📄', label: '解析正文内容' },
            { icon: '🌐', label: '爬取官网信息' },
            { icon: '🛡️', label: '规则引擎审核' },
            { icon: '🤖', label: 'LLM 语义审核' },
            { icon: '📊', label: '生成审核报告' },
        ];

        const renderProgress = () => {
            const elapsed = Math.floor((Date.now() - this._reviewStartTime) / 1000);
            const elapsedStr = elapsed >= 60 ? `${Math.floor(elapsed / 60)}分${elapsed % 60}秒` : `${elapsed}秒`;

            const stepsToShow = Math.min(steps.length - 1, Math.floor(elapsed / 5));

            const html = `
                <div class="card mt-lg" id="review-progress-card">
                    <div class="card-body" style="padding:24px;">
                        <div class="text-center mb-md">
                            <div class="spinner spinner-lg" style="margin:0 auto 12px;"></div>
                            <div class="text-bold" style="font-size:16px;">正在审核中... <span class="text-secondary text-sm">已用时 ${elapsedStr}</span></div>
                        </div>
                        <div style="max-width:400px;margin:0 auto;">
                            ${steps.map((step, idx) => {
                                let state = 'pending';
                                let icon = step.icon;
                                if (idx < stepsToShow) { state = 'done'; icon = '✓'; }
                                else if (idx === stepsToShow) { state = 'active'; }
                                return `
                                    <div class="d-flex align-items-center gap-sm" style="padding:6px 0;opacity:${state === 'pending' ? 0.4 : 1};transition:opacity 0.3s;">
                                        <span style="width:24px;height:24px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0;
                                            ${state === 'done' ? 'background:#10b981;color:#fff;' : ''}
                                            ${state === 'active' ? 'background:var(--color-primary);color:#fff;' : ''}
                                            ${state === 'pending' ? 'background:var(--color-border);color:var(--color-text-3);' : ''}
                                        ">${state === 'active' ? '<div class="spinner" style="width:12px;height:12px;border-width:2px;"></div>' : icon}</span>
                                        <span style="font-size:14px;${state === 'active' ? 'font-weight:600;color:var(--color-primary);' : ''}">${step.label}</span>
                                    </div>
                                `;
                            }).join('')}
                        </div>
                        <div class="text-center text-sm text-secondary mt-md">
                            审核时长取决于文本长度和 LLM 响应速度，请耐心等待
                        </div>
                    </div>
                </div>
            `;
            area.innerHTML = html;
            area.scrollIntoView({ behavior: 'smooth' });
        };

        renderProgress();

        this._reviewProgressTimer = setInterval(() => {
            renderProgress();
        }, 1000);
    },

    /**
     * 停止审核进度提示
     */
    _stopReviewProgress() {
        if (this._reviewProgressTimer) {
            clearInterval(this._reviewProgressTimer);
            this._reviewProgressTimer = null;
        }
        const card = document.getElementById('review-progress-card');
        if (card) card.remove();
    },

    /**
     * 渲染审核结果
     * @param {object} result - 审核响应数据
     */
    renderReviewResult(result) {
        const area = document.getElementById('review-result-area');
        if (!area) return;

        const issues = result.issues || [];
        const stats = result.stats || {};
        const verdict = result.verdict || 'failed';
        const status = result.status || 'completed';

        const html = `
            <div class="card mt-lg">
                <div class="card-header">
                    <span class="card-title">审核结果</span>
                    <div class="card-actions">
                        ${UI.badge(verdict)}
                        <span class="badge ${status === 'completed' ? 'badge-success' : status === 'partial' ? 'badge-warning' : 'badge-danger'}">
                            ${status === 'completed' ? '已完成' : status === 'partial' ? '部分完成' : '失败'}
                        </span>
                    </div>
                </div>
                <div class="card-body">
                    ${result.review_id ? `<div class="text-sm text-secondary mb-sm">审核 ID: <code>${UI.escapeHtml(result.review_id)}</code></div>` : ''}
                    <div class="text-sm text-secondary mb-md">审核时间: ${UI.formatDate(result.reviewed_at)}</div>

                    <!-- 审核计划摘要 (TaskPlanner) -->
                    ${result.plan_summary ? `
                        <div class="review-plan-card">
                            <div class="review-plan-header">
                                <div class="review-plan-icon">🎯</div>
                                <span class="review-plan-title">审核计划</span>
                            </div>
                            <div class="review-plan-items">
                                ${result.plan_summary.content_type ? `<span class="review-plan-item"><span class="plan-item-dot" style="background:#3b82f6;"></span>内容类型: ${UI.escapeHtml(result.plan_summary.content_type)}</span>` : ''}
                                ${result.plan_summary.industry ? `<span class="review-plan-item"><span class="plan-item-dot" style="background:#8b5cf6;"></span>行业: ${UI.escapeHtml(result.plan_summary.industry)}</span>` : ''}
                                ${result.plan_summary.rule_template ? `<span class="review-plan-item"><span class="plan-item-dot" style="background:#10b981;"></span>规则模板: ${UI.escapeHtml(result.plan_summary.rule_template)}</span>` : ''}
                                ${result.plan_summary.strategy ? `<span class="review-plan-item"><span class="plan-item-dot" style="background:#f59e0b;"></span>策略: ${UI.escapeHtml(result.plan_summary.strategy)}</span>` : ''}
                            </div>
                            ${result.plan_summary.strategies && result.plan_summary.strategies.length > 0 ? `
                                <div class="review-plan-items mt-sm">
                                    ${result.plan_summary.strategies.map(s => `<span class="review-plan-item"><span class="plan-item-dot" style="background:#6366f1;"></span>${UI.escapeHtml(s)}</span>`).join('')}
                                </div>
                            ` : ''}
                        </div>
                    ` : ''}

                    <!-- GEO 特色审核维度 -->
                    <div class="geo-feature-section">
                        <div class="geo-feature-title">🔍 GEO 特色审核</div>
                        <div class="geo-feature-cards">
                            ${(() => {
                                const geoIssues = issues.filter(i => {
                                    const t = (i.type || '').toLowerCase();
                                    return t.includes('citable') || t.includes('brand') || t.includes('entity') || t.includes('geo');
                                });
                                const citableIssues = issues.filter(i => (i.type || '').toLowerCase().includes('citable'));
                                const brandIssues = issues.filter(i => (i.type || '').toLowerCase().includes('brand') || (i.type || '').toLowerCase().includes('entity'));
                                return `
                                    <div class="geo-feature-card">
                                        <div class="geo-feature-card-header">
                                            <span>📎</span> LLM可引用性
                                        </div>
                                        <div class="geo-feature-card-body">
                                            ${citableIssues.length > 0
                                                ? `发现 <strong style="color:#dc2626;">${citableIssues.length}</strong> 个可引用性问题`
                                                : `未发现可引用性问题 <span style="color:#10b981;">✓</span>`
                                            }
                                            <div class="text-sm text-secondary mt-xs">检查实体明确性、权威来源、结构化信息、事实依据</div>
                                        </div>
                                    </div>
                                    <div class="geo-feature-card">
                                        <div class="geo-feature-card-header">
                                            <span>🏷️</span> 品牌实体一致性
                                        </div>
                                        <div class="geo-feature-card-body">
                                            ${brandIssues.length > 0
                                                ? `发现 <strong style="color:#dc2626;">${brandIssues.length}</strong> 个品牌一致性问题`
                                                : `未发现品牌一致性问题 <span style="color:#10b981;">✓</span>`
                                            }
                                            <div class="text-sm text-secondary mt-xs">检查实体识别准确性、产品描述一致性、能力边界清晰度</div>
                                        </div>
                                    </div>
                                `;
                            })()}
                        </div>
                    </div>

                    <!-- 联网事实核查 -->
                    ${(() => {
                        const factCheckIssues = issues.filter(i => {
                            const src = (i.evidence && i.evidence.reference_source) || '';
                            return src.includes('web_search') || src.includes('fact_check');
                        });
                        const hasWarning = (result.warnings || []).some(w => (w.code || '').includes('FACT_CHECK'));
                        const verdictMap = { 'verified': { label: '已证实', color: '#10b981', icon: '✅' }, 'refuted': { label: '已证伪', color: '#dc2626', icon: '❌' }, 'unverifiable': { label: '无法核实', color: '#f59e0b', icon: '⚠️' }, 'partially_verified': { label: '部分证实', color: '#f97316', icon: '🔍' } };
                        return `
                            <div class="geo-feature-section" style="margin-top:16px;">
                                <div class="geo-feature-title">🌐 联网事实核查</div>
                                <div class="geo-feature-cards">
                                    <div class="geo-feature-card" style="grid-column: 1 / -1;">
                                        <div class="geo-feature-card-header">
                                            <span>🔍</span> 联网搜索验证
                                        </div>
                                        <div class="geo-feature-card-body">
                                            ${factCheckIssues.length > 0
                                                ? `发现 <strong style="color:#dc2626;">${factCheckIssues.length}</strong> 个事实核查问题`
                                                : `所有事实声明均通过联网验证 <span style="color:#10b981;">✓</span>`
                                            }
                                            ${hasWarning ? `<div class="text-sm" style="color:#f59e0b;">⚠ 联网核查部分异常，建议人工复核</div>` : ''}
                                            <div class="text-sm text-secondary mt-xs">对正文中的关键事实性声明进行多引擎联网搜索核实（排名、奖项、数据、资质等）</div>
                                            ${factCheckIssues.length > 0 ? `
                                                <div class="mt-sm" style="display:flex;flex-direction:column;gap:8px;">
                                                    ${factCheckIssues.map(i => {
                                                        const sev = verdictMap[(i.severity === 'critical' ? 'refuted' : (i.severity === 'major' ? 'unverifiable' : 'unknown'))] || { label: '未知', color: '#999', icon: '❓' };
                                                        return `
                                                        <div style="padding:10px 14px;background:${i.severity === 'critical' ? '#fef2f2' : '#fffbeb'};border-radius:8px;border-left:4px solid ${i.severity === 'critical' ? '#dc2626' : '#f59e0b'};">
                                                            <div class="text-sm" style="font-weight:600;color:${i.severity === 'critical' ? '#dc2626' : '#b45309'};">
                                                                ${sev.icon} ${sev.label}: ${UI.escapeHtml(i.title)}
                                                            </div>
                                                            ${(i.evidence && i.evidence.snippet) ? `<div class="text-sm text-secondary mt-xs" style="padding:4px 8px;background:rgba(0,0,0,0.03);border-radius:4px;">原文: "${UI.escapeHtml(i.evidence.snippet.substring(0, 120))}"</div>` : ''}
                                                            ${i.reason ? `<div class="text-sm mt-xs" style="color:#374151;">🔎 ${UI.escapeHtml(i.reason.substring(0, 300))}</div>` : ''}
                                                            ${i.suggestion ? `<div class="text-sm mt-xs" style="color:#3b82f6;">💡 ${UI.escapeHtml(i.suggestion.substring(0, 200))}</div>` : ''}
                                                            ${i.evidence && i.evidence.source_url ? `<div class="text-sm mt-xs"><a href="${i.evidence.source_url}" target="_blank" style="color:#3b82f6;">📎 搜索来源</a></div>` : ''}
                                                        </div>
                                                    `}).join('')}
                                                </div>
                                            ` : ''}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        `;
                    })()}

                    <!-- 摘要 -->
                    <div class="form-group mt-md">
                        <label class="form-label">审核摘要</label>
                        <div style="padding:12px;background:var(--color-border-light);border-radius:6px;line-height:1.6;">
                            ${UI.escapeHtml(result.summary || '无摘要')}
                        </div>
                    </div>

                    <!-- 统计 -->
                    <div class="d-flex gap-md mb-md" style="flex-wrap:wrap;">
                        <div class="badge badge-default">总问题数: ${stats.total || issues.length || 0}</div>
                        <div class="badge badge-danger">CRITICAL: ${(stats.by_severity || {}).critical || 0}</div>
                        <div class="badge badge-warning">HIGH: ${(stats.by_severity || {}).major || 0}</div>
                        <div class="badge badge-info">MEDIUM: ${(stats.by_severity || {}).minor || 0}</div>
                        <div class="badge badge-default">LOW: ${(stats.by_severity || {}).info || 0}</div>
                        ${result.duration_ms ? `<div class="badge badge-default">耗时: ${(result.duration_ms / 1000).toFixed(1)}s</div>` : ''}
                    </div>

                    ${result.error ? `
                        <div class="card" style="border-color:var(--color-danger);">
                            <div class="card-body" style="color:var(--color-danger);">
                                <strong>错误信息:</strong> ${UI.escapeHtml(result.error.message || '未知错误')}
                                ${result.error.code ? ` <code>(${UI.escapeHtml(result.error.code)})</code>` : ''}
                            </div>
                        </div>
                    ` : ''}

                    <!-- 问题列表 -->
                    ${issues.length > 0 ? `
                        <div class="mt-md">
                            <div class="d-flex align-items-center justify-content-between mb-md">
                                <h3 class="text-lg text-bold">问题列表 (${issues.length})</h3>
                                <div class="d-flex gap-sm" style="flex-wrap:wrap;">
                                    <button class="btn btn-sm btn-secondary" id="export-text-btn">📄 导出文本</button>
                                    <button class="btn btn-sm btn-secondary" id="export-md-btn">📝 导出Markdown</button>
                                    <button class="btn btn-sm btn-secondary" id="expand-all-btn">展开全部</button>
                                    <button class="btn btn-sm btn-secondary" id="collapse-all-btn">收起全部</button>
                                </div>
                            </div>

                            <!-- 严重程度筛选栏 -->
                            <div class="d-flex gap-sm mb-md issue-filter-bar" style="flex-wrap:wrap;">
                                <button class="btn btn-sm btn-primary issue-filter-btn active" data-severity="all">全部 ${issues.length}</button>
                                <button class="btn btn-sm btn-secondary issue-filter-btn" data-severity="critical">🔴 CRITICAL ${(stats.by_severity || {}).critical || 0}</button>
                                <button class="btn btn-sm btn-secondary issue-filter-btn" data-severity="major">🟠 HIGH ${(stats.by_severity || {}).major || 0}</button>
                                <button class="btn btn-sm btn-secondary issue-filter-btn" data-severity="minor">🟡 MEDIUM ${(stats.by_severity || {}).minor || 0}</button>
                                <button class="btn btn-sm btn-secondary issue-filter-btn" data-severity="info">🔵 LOW ${(stats.by_severity || {}).info || 0}</button>
                            </div>

                            ${issues.map((issue, idx) => `
                                <div class="card issue-card" data-severity="${issue.severity || 'info'}" data-issue-idx="${idx}" style="margin-bottom:10px;border-left:3px solid ${UI._severityColor(issue.severity)};">
                                    <div class="card-header issue-header" style="cursor:pointer;user-select:none;padding:10px 16px;">
                                        <div class="d-flex align-items-center justify-content-between">
                                            <div class="d-flex align-items-center gap-sm" style="flex:1;min-width:0;">
                                                <span class="issue-toggle" style="font-size:10px;color:var(--color-text-3);transition:transform 0.2s;display:inline-block;">▶</span>
                                                <span class="text-bold" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${idx + 1}. ${UI.escapeHtml(issue.title || '未命名问题')}</span>
                                                ${UI.severityBadge(issue.severity)}
                                                <span class="badge badge-default" style="flex-shrink:0;">${UI.issueTypeText(issue.type)}</span>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="card-body issue-body" style="display:none;padding:16px;">
                                        <div class="d-flex align-items-center justify-content-between mb-sm">
                                            <span class="text-sm text-secondary">问题 ID: ${UI.escapeHtml(issue.id || '')}</span>
                                        </div>
                                        ${issue.evidence ? `
                                            <div class="mb-sm">
                                                <div style="font-size:12px; color:var(--color-text-3); margin-bottom:4px; font-weight:500;">📍 原文片段</div>
                                                <div class="code-block mt-xs" style="line-height:1.6;">${UI.escapeHtml(issue.evidence.snippet || '')}</div>
                                            </div>
                                        ` : ''}
                                        ${issue.reason ? `
                                            <div class="mb-sm">
                                                <div style="font-size:12px; color:var(--color-text-3); margin-bottom:4px; font-weight:500;">❓ 问题原因</div>
                                                <div style="color:var(--color-text-2); line-height:1.6; font-size:14px;">${UI.escapeHtml(issue.reason)}</div>
                                            </div>
                                        ` : ''}
                                        ${issue.evidence && issue.evidence.reference_detail ? `
                                            <div class="mb-sm text-sm">
                                                <span class="text-secondary">参考依据:</span> ${UI.escapeHtml(issue.evidence.reference_detail)}
                                            </div>
                                        ` : ''}
                                        ${issue.suggestion ? `
                                            <div style="background:var(--color-success-bg, #e6f7ed); padding:10px 12px; border-radius:6px;position:relative;">
                                                <div class="d-flex align-items-center justify-content-between" style="margin-bottom:4px;">
                                                    <span style="font-size:12px; color:var(--color-success); font-weight:500;">💡 修改建议</span>
                                                    <button class="btn btn-text btn-sm copy-suggestion-btn" data-suggestion="${UI.escapeHtml(issue.suggestion)}" style="font-size:12px;padding:2px 8px;color:var(--color-primary);">复制</button>
                                                </div>
                                                <div style="color:var(--color-text-1); line-height:1.6; font-size:14px;">${UI.escapeHtml(issue.suggestion)}</div>
                                            </div>
                                        ` : ''}
                                    </div>
                                </div>
                            `).join('')}
                        </div>
                    ` : '<div class="empty-state" style="padding:24px;"><div class="empty-icon">✓</div><div>未发现审核问题</div></div>'}

                    <!-- 修改清单 -->
                    ${result.revision_checklist && result.revision_checklist.length > 0 ? `
                        <div class="mt-md">
                            <h3 class="text-lg text-bold mb-md">修改清单</h3>
                            <ol style="list-style:decimal;padding-left:20px;">
                                ${result.revision_checklist.map(item => `<li style="margin-bottom:8px;line-height:1.6;">${UI.escapeHtml(item)}</li>`).join('')}
                            </ol>
                        </div>
                    ` : ''}

                    <!-- 警告 -->
                    ${result.warnings && result.warnings.length > 0 ? `
                        <div class="mt-md">
                            <h3 class="text-lg text-bold mb-md text-warning">处理警告</h3>
                            ${result.warnings.map(w => `
                                <div class="badge badge-warning" style="display:block;margin-bottom:4px;text-align:left;">
                                    ${UI.escapeHtml(w.code)}: ${UI.escapeHtml(w.message)}
                                </div>
                            `).join('')}
                        </div>
                    ` : ''}

                    <!-- 完整结果（文本） -->
                    <div class="mt-md">
                        <div class="d-flex align-items-center justify-content-between mb-sm">
                            <h3 class="text-lg text-bold">完整结果</h3>
                            <button class="btn btn-sm btn-secondary" id="toggle-text">展开/收起</button>
                        </div>
                        <div id="full-text-viewer" style="display:none;">
                            <div class="code-block" style="white-space:pre-wrap;max-height:500px;overflow-y:auto;">${UI.escapeHtml(this.resultToText(result))}</div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        area.innerHTML = html;

        // 绑定文本展开/收起
        const toggleBtn = document.getElementById('toggle-text');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', () => {
                const viewer = document.getElementById('full-text-viewer');
                viewer.style.display = viewer.style.display === 'none' ? 'block' : 'none';
            });
        }

        // 绑定问题卡片折叠/展开
        area.querySelectorAll('.issue-header').forEach(header => {
            header.addEventListener('click', () => {
                const body = header.nextElementSibling;
                const toggle = header.querySelector('.issue-toggle');
                if (body.style.display === 'none') {
                    body.style.display = 'block';
                    if (toggle) toggle.style.transform = 'rotate(90deg)';
                } else {
                    body.style.display = 'none';
                    if (toggle) toggle.style.transform = 'rotate(0deg)';
                }
            });
        });

        // 绑定全部展开/收起
        const expandAllBtn = document.getElementById('expand-all-btn');
        if (expandAllBtn) {
            expandAllBtn.addEventListener('click', () => {
                area.querySelectorAll('.issue-body').forEach(body => {
                    body.style.display = 'block';
                });
                area.querySelectorAll('.issue-toggle').forEach(t => t.style.transform = 'rotate(90deg)');
            });
        }
        const collapseAllBtn = document.getElementById('collapse-all-btn');
        if (collapseAllBtn) {
            collapseAllBtn.addEventListener('click', () => {
                area.querySelectorAll('.issue-body').forEach(body => {
                    body.style.display = 'none';
                });
                area.querySelectorAll('.issue-toggle').forEach(t => t.style.transform = 'rotate(0deg)');
            });
        }

        // 绑定严重程度筛选
        area.querySelectorAll('.issue-filter-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                // 更新按钮激活状态
                area.querySelectorAll('.issue-filter-btn').forEach(b => {
                    b.classList.remove('active', 'btn-primary');
                    b.classList.add('btn-secondary');
                });
                btn.classList.add('active', 'btn-primary');
                btn.classList.remove('btn-secondary');

                const sev = btn.dataset.severity;
                area.querySelectorAll('.issue-card').forEach(card => {
                    if (sev === 'all' || card.dataset.severity === sev) {
                        card.style.display = '';
                    } else {
                        card.style.display = 'none';
                    }
                });
            });
        });

        // 默认展开第一个 CRITICAL 问题
        const firstCritical = area.querySelector('.issue-card[data-severity="critical"] .issue-header');
        if (firstCritical) {
            firstCritical.click();
        } else {
            // 没有 critical 则展开第一个问题
            const firstIssue = area.querySelector('.issue-card .issue-header');
            if (firstIssue) firstIssue.click();
        }

        // 绑定复制修改建议
        area.querySelectorAll('.copy-suggestion-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const text = btn.dataset.suggestion || '';
                navigator.clipboard.writeText(text).then(() => {
                    const orig = btn.textContent;
                    btn.textContent = '✓ 已复制';
                    setTimeout(() => { btn.textContent = orig; }, 1500);
                }).catch(() => {
                    UI.toast('复制失败，请手动选择文本复制', 'warning');
                });
            });
        });

        // 绑定导出文本报告
        const exportTextBtn = document.getElementById('export-text-btn');
        if (exportTextBtn) {
            exportTextBtn.addEventListener('click', () => {
                const text = this.resultToText(result);
                this._downloadFile(text, `审核报告_${result.review_id || Date.now()}.txt`, 'text/plain');
                UI.toast('文本报告已导出', 'success');
            });
        }

        // 绑定导出 Markdown 报告
        const exportMdBtn = document.getElementById('export-md-btn');
        if (exportMdBtn) {
            exportMdBtn.addEventListener('click', () => {
                const md = this.resultToMarkdown(result);
                this._downloadFile(md, `审核报告_${result.review_id || Date.now()}.md`, 'text/markdown');
                UI.toast('Markdown 报告已导出', 'success');
            });
        }

        // 滚动到结果区域
        area.scrollIntoView({ behavior: 'smooth' });
    },

    /**
     * 将审核结果转为可读的纯文本格式
     * @param {object} result - 审核响应数据
     * @returns {string}
     */
    resultToText(result) {
        const lines = [];
        const verdictMap = { pass: '通过', revise: '需修改', reject: '拒绝发布', failed: '失败' };
        const statusMap = { completed: '已完成', partial: '部分完成', failed: '失败' };

        lines.push('═══════════════════════════════════════');
        lines.push('           GEO 生文审核报告');
        lines.push('═══════════════════════════════════════');
        lines.push('');

        if (result.review_id) {
            lines.push(`审核 ID: ${result.review_id}`);
        }
        lines.push(`审核时间: ${UI.formatDate(result.reviewed_at)}`);
        lines.push(`审核结论: ${verdictMap[result.verdict] || result.verdict || '未知'}`);
        lines.push(`审核状态: ${statusMap[result.status] || result.status || '未知'}`);
        if (result.duration_ms) {
            lines.push(`耗时: ${(result.duration_ms / 1000).toFixed(1)} 秒`);
        }
        lines.push('');

        // 摘要
        lines.push('───────────────────────────────────────');
        lines.push('【审核摘要】');
        lines.push(result.summary || '无摘要');
        lines.push('');

        // 统计
        const stats = result.stats || {};
        lines.push('───────────────────────────────────────');
        lines.push('【问题统计】');
        lines.push(`  总问题数: ${stats.total || (result.issues || []).length || 0}`);
        const bySev = stats.by_severity || {};
        lines.push(`  严重: ${bySev.critical || 0}  |  重要: ${bySev.major || 0}  |  次要: ${bySev.minor || 0}  |  提示: ${bySev.info || 0}`);
        lines.push('');

        // 问题列表
        const issues = result.issues || [];
        if (issues.length > 0) {
            lines.push('───────────────────────────────────────');
            lines.push(`【问题列表】(共 ${issues.length} 项)`);
            lines.push('');
            issues.forEach((issue, idx) => {
                const sevMap = { critical: 'CRITICAL', major: 'HIGH', minor: 'MEDIUM', info: 'LOW' };
                lines.push(`  ${idx + 1}. ${issue.title || '未命名问题'}`);
                lines.push(`     严重程度: ${sevMap[issue.severity] || issue.severity || '未知'}`);
                lines.push(`     问题类型: ${UI.issueTypeText(issue.type)}`);
                if (issue.id) lines.push(`     问题 ID: ${issue.id}`);
                if (issue.evidence && issue.evidence.snippet) {
                    lines.push(`     原文片段: ${issue.evidence.snippet}`);
                }
                if (issue.reason) {
                    lines.push(`     问题原因: ${issue.reason}`);
                }
                if (issue.evidence && issue.evidence.reference_detail) {
                    lines.push(`     参考依据: ${issue.evidence.reference_detail}`);
                }
                lines.push(`     修改建议: ${issue.suggestion || '无'}`);
                lines.push('');
            });
        } else {
            lines.push('───────────────────────────────────────');
            lines.push('【问题列表】未发现审核问题');
            lines.push('');
        }

        // 修改清单
        if (result.revision_checklist && result.revision_checklist.length > 0) {
            lines.push('───────────────────────────────────────');
            lines.push('【修改清单】');
            result.revision_checklist.forEach((item, idx) => {
                lines.push(`  ${idx + 1}. ${item}`);
            });
            lines.push('');
        }

        // 警告
        if (result.warnings && result.warnings.length > 0) {
            lines.push('───────────────────────────────────────');
            lines.push('【处理警告】');
            result.warnings.forEach(w => {
                lines.push(`  [${w.code}] ${w.message}`);
            });
            lines.push('');
        }

        // 错误信息
        if (result.error) {
            lines.push('───────────────────────────────────────');
            lines.push('【错误信息】');
            lines.push(`  错误: ${result.error.message || '未知错误'}`);
            if (result.error.code) lines.push(`  代码: ${result.error.code}`);
            lines.push('');
        }

        lines.push('═══════════════════════════════════════');
        lines.push('           报告结束');
        lines.push('═══════════════════════════════════════');

        return lines.join('\n');
    },

    /**
     * 将审核结果转为 Markdown 格式
     */
    resultToMarkdown(result) {
        const verdictMap = { pass: '✅ 通过', revise: '⚠️ 需修改', reject: '❌ 拒绝发布', failed: '❌ 失败' };
        const sevMap = { critical: '🔴 CRITICAL', major: '🟠 HIGH', minor: '🟡 MEDIUM', info: '🔵 LOW' };
        const stats = result.stats || {};
        const bySev = stats.by_severity || {};
        const issues = result.issues || [];

        let md = `# GEO 生文审核报告\n\n`;
        if (result.review_id) md += `> **审核 ID**: ${result.review_id}\n`;
        md += `> **审核时间**: ${UI.formatDate(result.reviewed_at)}\n`;
        md += `> **审核结论**: ${verdictMap[result.verdict] || result.verdict || '未知'}\n`;
        if (result.duration_ms) md += `> **耗时**: ${(result.duration_ms / 1000).toFixed(1)} 秒\n`;
        md += `\n`;

        md += `## 📊 问题统计\n\n`;
        md += `| 严重程度 | 数量 |\n|----------|------|\n`;
        md += `| 总计 | ${stats.total || issues.length || 0} |\n`;
        md += `| 🔴 CRITICAL | ${bySev.critical || 0} |\n`;
        md += `| 🟠 HIGH | ${bySev.major || 0} |\n`;
        md += `| 🟡 MEDIUM | ${bySev.minor || 0} |\n`;
        md += `| 🔵 LOW | ${bySev.info || 0} |\n\n`;

        md += `## 📝 审核摘要\n\n${result.summary || '无摘要'}\n\n`;

        if (issues.length > 0) {
            md += `## 🐛 问题列表（共 ${issues.length} 项）\n\n`;
            issues.forEach((issue, idx) => {
                md += `### ${idx + 1}. ${issue.title || '未命名问题'}\n\n`;
                md += `- **严重程度**: ${sevMap[issue.severity] || issue.severity || '未知'}\n`;
                md += `- **问题类型**: ${UI.issueTypeText(issue.type)}\n`;
                if (issue.id) md += `- **问题 ID**: \`${issue.id}\`\n`;
                if (issue.evidence && issue.evidence.snippet) {
                    md += `\n**📍 原文片段:**\n\`\`\`\n${issue.evidence.snippet}\n\`\`\`\n`;
                }
                if (issue.reason) {
                    md += `\n**❓ 问题原因:**\n${issue.reason}\n`;
                }
                if (issue.evidence && issue.evidence.reference_detail) {
                    md += `\n**📎 参考依据:** ${issue.evidence.reference_detail}\n`;
                }
                if (issue.suggestion) {
                    md += `\n**💡 修改建议:**\n${issue.suggestion}\n`;
                }
                md += `\n---\n\n`;
            });
        }

        if (result.revision_checklist && result.revision_checklist.length > 0) {
            md += `## ✅ 修改清单\n\n`;
            result.revision_checklist.forEach((item, idx) => {
                md += `${idx + 1}. ${item}\n`;
            });
            md += `\n`;
        }

        if (result.warnings && result.warnings.length > 0) {
            md += `## ⚠️ 处理警告\n\n`;
            result.warnings.forEach(w => {
                md += `- **${w.code}**: ${w.message}\n`;
            });
        }

        return md;
    },

    /**
     * 下载文件辅助方法
     */
    _downloadFile(content, filename, mimeType) {
        const blob = new Blob([content], { type: `${mimeType};charset=utf-8` });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    },

    /* --------------------------------------------------------
     * 历史记录列表
     * -------------------------------------------------------- */
    async renderHistory(page = 1, filters = {}) {
        App.updateContent(UI.skeleton(5));

        const params = {
            page,
            page_size: CONFIG.DEFAULT_PAGE_SIZE,
            ...filters,
        };

        try {
            const res = await ApiClient.get('/history', params);
            const data = res.data || [];
            const total = res.total || 0;

            // 快捷时间筛选日期计算
            const now = new Date();
            const pad = (n) => String(n).padStart(2, '0');
            const fmt = (d) => `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
            const todayStr = fmt(now);
            const weekStart = new Date(now);
            const dayOfWeek = now.getDay() || 7; // 周日=7
            weekStart.setDate(now.getDate() - dayOfWeek + 1);
            const weekStartStr = fmt(weekStart);
            const monthStartStr = `${now.getFullYear()}-${pad(now.getMonth()+1)}-01`;
            const activeRange = filters.start_date === todayStr ? 'today'
                : filters.start_date === weekStartStr ? 'week'
                : filters.start_date === monthStartStr ? 'month'
                : 'all';

            const html = `
                <div class="d-flex align-items-center justify-content-between mb-lg">
                    <h2 style="font-size:20px;font-weight:600;">审核历史</h2>
                    <button class="btn btn-danger btn-sm" id="batch-delete-btn" disabled>批量删除</button>
                </div>

                <!-- 筛选条件 — 顶部精简：审核结论 / 审核状态 / 审核标题 / 提报表 + 搜索按钮 -->
                <div class="card mb-lg">
                    <div class="card-body">
                        <div class="form-row" style="align-items:flex-end;">
                            <div class="form-group" style="flex:0 0 13%;">
                                <label class="form-label">审核结论</label>
                                <select id="filter-verdict">
                                    <option value="">全部</option>
                                    <option value="pass" ${filters.verdict === 'pass' ? 'selected' : ''}>通过</option>
                                    <option value="revise" ${filters.verdict === 'revise' ? 'selected' : ''}>需修改</option>
                                    <option value="reject" ${filters.verdict === 'reject' ? 'selected' : ''}>拒绝发布</option>
                                </select>
                            </div>
                            <div class="form-group" style="flex:0 0 13%;">
                                <label class="form-label">审核状态</label>
                                <select id="filter-status">
                                    <option value="">全部</option>
                                    <option value="completed" ${filters.status === 'completed' ? 'selected' : ''}>已完成</option>
                                    <option value="partial" ${filters.status === 'partial' ? 'selected' : ''}>部分完成</option>
                                    <option value="failed" ${filters.status === 'failed' ? 'selected' : ''}>失败</option>
                                </select>
                            </div>
                            <div class="form-group" style="flex:0 0 13%;">
                                <label class="form-label">审核标题</label>
                                <input type="text" id="filter-content-title" value="${UI.escapeHtml(filters.content_title || '')}" placeholder="按标题关键词搜索">
                            </div>
                            <div class="form-group" style="flex:0 0 13%;">
                                <label class="form-label">提报表</label>
                                <input type="text" id="filter-submission-name" value="${UI.escapeHtml(filters.submission_name || '')}" placeholder="如 永安期货">
                            </div>
                            <div class="form-group" style="flex:0 0 auto;">
                                <button class="btn btn-primary" id="filter-search-btn" style="margin-bottom:0;">搜索</button>
                            </div>
                        </div>
                        <div class="d-flex gap-sm mt-md" style="align-items:center;">
                            <span class="text-sm text-secondary">快捷筛选：</span>
                            <button class="btn btn-sm ${activeRange === 'all' ? 'btn-primary' : 'btn-secondary'} time-filter-btn" data-range="all">全部</button>
                            <button class="btn btn-sm ${activeRange === 'today' ? 'btn-primary' : 'btn-secondary'} time-filter-btn" data-range="today">今日</button>
                            <button class="btn btn-sm ${activeRange === 'week' ? 'btn-primary' : 'btn-secondary'} time-filter-btn" data-range="week">本周</button>
                            <button class="btn btn-sm ${activeRange === 'month' ? 'btn-primary' : 'btn-secondary'} time-filter-btn" data-range="month">本月</button>
                        </div>
                    </div>
                </div>

                <!-- 历史列表 -->
                <div class="card">
                    <div class="card-body p-0">
                        ${data.length === 0 ? UI.emptyState('暂无审核记录', '📋') : `
                            <div class="table-wrapper" style="border:none;border-radius:0;">
                                <table class="table-hover">
                                    <thead>
                                        <tr>
                                            <th style="width:32px;"><input type="checkbox" id="select-all"></th>
                                            <th style="width:30%;">审核标题</th>
                                            <th style="width:18%;">提报表</th>
                                            <th style="width:8%;">结论</th>
                                            <th style="width:8%;">状态</th>
                                            <th style="width:6%;">问题数</th>
                                            <th style="width:6%;">严重</th>
                                            <th style="width:14%;">审核时间</th>
                                            <th style="width:10%;">操作</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${data.map(r => {
                                            const title = r.content_title || r.task_name || '未命名';
                                            const sub = r.submission_summary || {};
                                            const subInfo = sub.company_name ? sub.company_name : (sub.filename ? sub.filename : '-');
                                            return `
                                            <tr data-id="${r.review_id || r.id}">
                                                <td><input type="checkbox" class="row-checkbox" value="${r.review_id || r.id}"></td>
                                                <td>
                                                    <div style="font-weight:500;color:#2c3e50;margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${UI.escapeHtml(title)}">${UI.escapeHtml(title)}</div>
                                                    <div style="font-size:12px;color:#95a5a6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${UI.escapeHtml(r.content_preview || '')}">${UI.escapeHtml((r.content_preview || '').slice(0, 50))}${r.content_preview && r.content_preview.length > 50 ? '...' : ''}</div>
                                                </td>
                                                <td>
                                                    <div style="font-size:13px;color:#34495e;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${UI.escapeHtml(subInfo)}">${UI.escapeHtml(subInfo)}</div>
                                                    ${sub.task_name ? `<div style="font-size:12px;color:#95a5a6;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${UI.escapeHtml(sub.task_name)}">${UI.escapeHtml(sub.task_name)}</div>` : ''}
                                                </td>
                                                <td>${UI.badge(r.verdict)}</td>
                                                <td>
                                                    <span class="badge ${r.status === 'completed' ? 'badge-success' : r.status === 'partial' ? 'badge-warning' : 'badge-danger'}">
                                                        ${r.status === 'completed' ? '完成' : r.status === 'partial' ? '部分' : '失败'}
                                                    </span>
                                                </td>
                                                <td>${r.total_issues || 0}</td>
                                                <td>${r.critical_issues || 0}</td>
                                                <td class="text-sm text-secondary">${UI.formatDate(r.reviewed_at)}</td>
                                                <td>
                                                    <div class="table-actions">
                                                        <button class="btn btn-link btn-sm" data-action="view" data-id="${r.review_id || r.id}">详情</button>
                                                        <button class="btn btn-link btn-sm text-danger" data-action="delete" data-id="${r.review_id || r.id}">删除</button>
                                                    </div>
                                                </td>
                                            </tr>
                                        `}).join('')}
                                    </tbody>
                                </table>
                            </div>
                        `}
                    </div>
                    ${data.length > 0 ? `<div class="card-footer">${UI.pagination(total, page, CONFIG.DEFAULT_PAGE_SIZE, (p) => this.renderHistory(p, filters))}</div>` : ''}
                </div>
            `;

            App.updateContent(html);
            this.bindHistoryEvents(filters);
        } catch (err) {
            App.updateContent(`
                <div class="empty-state">
                    <div class="empty-icon">⚠️</div>
                    <div class="mb-md">加载失败：${UI.escapeHtml(err.message)}</div>
                    <button class="btn btn-primary" onclick="location.reload()">重新加载</button>
                </div>
            `);
        }
    },

    /** 绑定历史列表事件 */
    bindHistoryEvents(filters) {
        // 日期计算工具
        const pad = (n) => String(n).padStart(2, '0');
        const fmt = (d) => `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
        const now = new Date();
        const todayStr = fmt(now);
        const weekStart = new Date(now);
        const dow = now.getDay() || 7;
        weekStart.setDate(now.getDate() - dow + 1);
        const weekStartStr = fmt(weekStart);
        const monthStartStr = `${now.getFullYear()}-${pad(now.getMonth()+1)}-01`;

        // 根据当前激活的时间标签计算 start_date
        const getActiveRange = () => {
            const active = document.querySelector('.time-filter-btn.btn-primary');
            return active ? active.dataset.range : 'all';
        };

        const getDateRange = (range) => {
            switch (range) {
                case 'today': return { start_date: todayStr };
                case 'week': return { start_date: weekStartStr };
                case 'month': return { start_date: monthStartStr };
                default: return { start_date: undefined, end_date: undefined };
            }
        };

        // 收集当前筛选值（含时间范围）
        const collectFilters = () => {
            const { start_date, end_date } = getDateRange(getActiveRange());
            return {
                verdict: document.getElementById('filter-verdict').value || undefined,
                status: document.getElementById('filter-status').value || undefined,
                content_title: document.getElementById('filter-content-title').value.trim() || undefined,
                submission_name: document.getElementById('filter-submission-name').value.trim() || undefined,
                start_date,
                end_date,
            };
        };

        // 搜索按钮点击
        document.getElementById('filter-search-btn')?.addEventListener('click', () => {
            this.renderHistory(1, collectFilters());
        });

        // 文本框回车触发搜索
        const enterHandler = (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this.renderHistory(1, collectFilters());
            }
        };
        document.getElementById('filter-content-title')?.addEventListener('keydown', enterHandler);
        document.getElementById('filter-submission-name')?.addEventListener('keydown', enterHandler);

        // 快捷时间筛选
        document.querySelectorAll('.time-filter-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.time-filter-btn').forEach(b => b.classList.replace('btn-primary', 'btn-secondary'));
                btn.classList.replace('btn-secondary', 'btn-primary');
                this.renderHistory(1, collectFilters());
            });
        });

        // 查看详情
        document.querySelectorAll('[data-action="view"]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const id = e.currentTarget.dataset.id;
                Router.navigate(`/history/detail?id=${id}`);
            });
        });

        // 删除单条
        document.querySelectorAll('[data-action="delete"]').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const id = e.currentTarget.dataset.id;
                const confirmed = await UI.confirm('确定要删除这条审核记录吗？', { danger: true });
                if (!confirmed) return;

                try {
                    UI.loading(true, '正在删除...');
                    await ApiClient.delete(`/history/${id}`);
                    UI.toast('删除成功', 'success');
                    // 重新加载当前页
                    const hash = Router.parseHash();
                    this.renderHistory(hash.query.page ? parseInt(hash.query.page) : 1, filters);
                } catch (err) {
                    UI.toast(err.message || '删除失败', 'error');
                } finally {
                    UI.loading(false);
                }
            });
        });

        // 行点击查看详情
        document.querySelectorAll('tr[data-id]').forEach(tr => {
            tr.addEventListener('click', (e) => {
                if (e.target.closest('input') || e.target.closest('button')) return;
                const id = tr.dataset.id;
                Router.navigate(`/history/detail?id=${id}`);
            });
        });

        // 全选
        const selectAll = document.getElementById('select-all');
        if (selectAll) {
            selectAll.addEventListener('change', (e) => {
                document.querySelectorAll('.row-checkbox').forEach(cb => {
                    cb.checked = e.target.checked;
                });
                this._updateBatchDeleteBtn();
            });
        }

        // 单行复选框
        document.querySelectorAll('.row-checkbox').forEach(cb => {
            cb.addEventListener('change', () => this._updateBatchDeleteBtn());
        });

        // 批量删除
        const batchBtn = document.getElementById('batch-delete-btn');
        if (batchBtn) {
            batchBtn.addEventListener('click', async () => {
                const ids = Array.from(document.querySelectorAll('.row-checkbox:checked')).map(cb => cb.value);
                if (ids.length === 0) return;

                const confirmed = await UI.confirm(`确定要删除选中的 ${ids.length} 条记录吗？`, { danger: true });
                if (!confirmed) return;

                try {
                    UI.loading(true, '正在删除...');
                    await ApiClient.post('/history/batch-delete', ids);
                    UI.toast(`成功删除 ${ids.length} 条记录`, 'success');
                    this.renderHistory(1, filters);
                } catch (err) {
                    UI.toast(err.message || '批量删除失败', 'error');
                } finally {
                    UI.loading(false);
                }
            });
        }
    },

    /** 更新批量删除按钮状态 */
    _updateBatchDeleteBtn() {
        const checked = document.querySelectorAll('.row-checkbox:checked').length;
        const btn = document.getElementById('batch-delete-btn');
        if (btn) {
            btn.disabled = checked === 0;
            btn.textContent = checked > 0 ? `批量删除 (${checked})` : '批量删除';
        }
    },

    /* --------------------------------------------------------
     * 历史记录详情
     * -------------------------------------------------------- */
    async renderHistoryDetail(id) {
        App.updateContent(UI.skeleton(5));

        try {
            const result = await ApiClient.get(`/history/${id}`);
            const issues = result.issues || [];
            const history = result.history || result;

            const html = `
                <div class="d-flex align-items-center justify-content-between mb-lg">
                    <div class="d-flex align-items-center gap-md">
                        <button class="btn btn-secondary btn-sm" id="back-to-history">← 返回</button>
                        <h2 style="font-size:18px;font-weight:600;">审核详情</h2>
                    </div>
                    <div class="d-flex gap-sm align-items-center">
                        ${UI.badge(history.verdict)}
                        <button class="btn btn-sm btn-secondary" id="detail-copy-btn">📋 复制结论</button>
                        <button class="btn btn-sm btn-secondary" id="detail-export-md-btn">📝 导出报告</button>
                    </div>
                </div>

                <!-- 基本信息 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">基本信息</span>
                    </div>
                    <div class="card-body">
                        <div class="form-group">
                            <label class="form-label text-secondary">审核标题</label>
                            <div style="font-size:15px;font-weight:500;color:#2c3e50;">${UI.escapeHtml(history.content_title || history.task_name || '未命名')}</div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label text-secondary">审核 ID</label>
                                <div class="text-mono text-sm">${UI.escapeHtml(history.review_id || history.id || id)}</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label text-secondary">任务名称</label>
                                <div>${UI.escapeHtml(history.task_name || '-')}</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label text-secondary">公司名称</label>
                                <div>${UI.escapeHtml(history.company_name || '-')}</div>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label text-secondary">审核状态</label>
                                <div>
                                    <span class="badge ${history.status === 'completed' ? 'badge-success' : history.status === 'partial' ? 'badge-warning' : 'badge-danger'}">
                                        ${history.status === 'completed' ? '已完成' : history.status === 'partial' ? '部分完成' : '失败'}
                                    </span>
                                </div>
                            </div>
                            <div class="form-group">
                                <label class="form-label text-secondary">审核时间</label>
                                <div>${UI.formatDate(history.reviewed_at)}</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label text-secondary">耗时</label>
                                <div>${history.duration_ms ? (history.duration_ms / 1000).toFixed(1) + 's' : '-'}</div>
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label text-secondary">正文来源</label>
                                <div>${UI.escapeHtml(history.content_source || '-')}</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label text-secondary">提报来源</label>
                                <div>${UI.escapeHtml(history.submission_source || '-')}</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label text-secondary">提交人</label>
                                <div>${UI.escapeHtml(history.submitted_by || '-')}</div>
                            </div>
                        </div>
                        ${history.batch_id ? `
                            <div class="form-group">
                                <label class="form-label text-secondary">批量任务 ID</label>
                                <div class="text-mono text-sm">${UI.escapeHtml(history.batch_id)}</div>
                            </div>
                        ` : ''}
                    </div>
                </div>

                <!-- 摘要 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">审核摘要</span>
                    </div>
                    <div class="card-body">
                        <div style="line-height:1.6;">${UI.escapeHtml(history.summary || '无摘要')}</div>
                    </div>
                </div>

                <!-- 统计 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">问题统计</span>
                    </div>
                    <div class="card-body">
                        <div class="d-flex gap-md" style="flex-wrap:wrap;">
                            <div class="badge badge-default">总数: ${history.total_issues || 0}</div>
                            <div class="badge badge-danger">严重: ${history.critical_issues || 0}</div>
                            <div class="badge badge-warning">重要: ${history.major_issues || 0}</div>
                            <div class="badge badge-info">次要: ${history.minor_issues || 0}</div>
                            <div class="badge badge-default">提示: ${history.info_issues || 0}</div>
                        </div>
                    </div>
                </div>

                <!-- 审核原文 -->
                ${history.content_text ? `
                    <div class="card">
                        <div class="card-header">
                            <span class="card-title">审核原文</span>
                        </div>
                        <div class="card-body">
                            <div class="code-block" style="max-height:400px;overflow-y:auto;">${UI.escapeHtml(history.content_text)}</div>
                        </div>
                    </div>
                ` : history.content_preview ? `
                    <div class="card">
                        <div class="card-header">
                            <span class="card-title">正文预览</span>
                        </div>
                        <div class="card-body">
                            <div class="code-block" style="max-height:300px;overflow-y:auto;">${UI.escapeHtml(history.content_preview)}</div>
                        </div>
                    </div>
                ` : ''}

                <!-- 提报表信息 -->
                ${history.submission_data ? `
                    <div class="card">
                        <div class="card-header">
                            <span class="card-title">提报表信息</span>
                        </div>
                        <div class="card-body">
                            <div class="code-block" style="max-height:300px;overflow-y:auto;">${UI.escapeHtml(typeof history.submission_data === 'string' ? history.submission_data : JSON.stringify(history.submission_data, null, 2))}</div>
                        </div>
                    </div>
                ` : ''}

                <!-- 官网URL -->
                ${history.official_urls && history.official_urls.length > 0 ? `
                    <div class="card">
                        <div class="card-header">
                            <span class="card-title">官网URL</span>
                        </div>
                        <div class="card-body">
                            <ul class="list">
                                ${history.official_urls.map(url => `<li><a href="${UI.escapeHtml(url)}" target="_blank">${UI.escapeHtml(url)}</a></li>`).join('')}
                            </ul>
                        </div>
                    </div>
                ` : ''}

                <!-- 问题列表 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">问题详情 (${issues.length})</span>
                    </div>
                    <div class="card-body">
                        ${issues.length === 0 ? UI.emptyState('未发现审核问题', '✓') : issues.map((issue, idx) => `
                            <div class="card" style="margin-bottom:16px; border-left: 4px solid var(--color-${issue.severity === 'critical' ? 'danger' : issue.severity === 'major' ? 'warning' : issue.severity === 'minor' ? 'info' : 'secondary'});">
                                <div class="card-body">
                                    <!-- 标题行 -->
                                    <div class="d-flex align-items-center justify-content-between mb-sm">
                                        <div class="d-flex align-items-center gap-sm">
                                            <span class="text-bold" style="font-size:1.05em;">${idx + 1}. ${UI.escapeHtml(issue.title || '未命名问题')}</span>
                                            ${UI.severityBadge(issue.severity)}
                                            <span class="badge badge-default">${UI.issueTypeText(issue.type)}</span>
                                        </div>
                                        <span class="text-sm text-secondary">${UI.escapeHtml(issue.issue_id || issue.id || '')}</span>
                                    </div>

                                    <!-- 原文片段 -->
                                    ${issue.snippet ? `
                                        <div class="mb-md" style="background:var(--color-bg-2); padding:12px; border-radius:6px;">
                                            <div style="font-size:12px; color:var(--color-text-3); margin-bottom:6px; font-weight:500;">📍 原文片段</div>
                                            <div style="color:var(--color-text-1); line-height:1.6;">${UI.escapeHtml(issue.snippet)}</div>
                                        </div>
                                    ` : ''}

                                    <!-- 原因分析 -->
                                    ${issue.reason ? `
                                        <div class="mb-md">
                                            <div style="font-size:12px; color:var(--color-text-3); margin-bottom:4px; font-weight:500;">❓ 问题原因</div>
                                            <div style="color:var(--color-text-2); line-height:1.6;">${UI.escapeHtml(issue.reason)}</div>
                                        </div>
                                    ` : ''}

                                    <!-- 修改建议 -->
                                    ${issue.suggestion ? `
                                        <div style="background:var(--color-success-bg, #e6f7ed); padding:12px; border-radius:6px;">
                                            <div style="font-size:12px; color:var(--color-success); margin-bottom:4px; font-weight:500;">💡 修改建议</div>
                                            <div style="color:var(--color-text-1); line-height:1.6;">${UI.escapeHtml(issue.suggestion)}</div>
                                        </div>
                                    ` : ''}
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>

                <!-- 错误信息 -->
                ${history.error_message ? `
                    <div class="card" style="border-color:var(--color-danger);">
                        <div class="card-header">
                            <span class="card-title text-danger">错误信息</span>
                        </div>
                        <div class="card-body">
                            ${history.error_code ? `<div class="text-sm text-secondary mb-sm">错误代码: <code>${UI.escapeHtml(history.error_code)}</code></div>` : ''}
                            <div style="color:var(--color-danger);">${UI.escapeHtml(history.error_message)}</div>
                        </div>
                    </div>
                ` : ''}

                <!-- 完整数据 -->
                ${history.result_data ? `
                    <div class="card">
                        <div class="card-header">
                            <span class="card-title">完整审核数据</span>
                            <button class="btn btn-sm btn-secondary" id="toggle-detail-text">展开/收起</button>
                        </div>
                        <div class="card-body">
                            <div id="detail-text-viewer" style="display:none;">
                                <div class="code-block" style="white-space:pre-wrap;max-height:500px;overflow-y:auto;">${UI.escapeHtml(this.resultToText(history.result_data))}</div>
                            </div>
                        </div>
                    </div>
                ` : ''}
            `;

            App.updateContent(html);

            // 返回按钮
            const backBtn = document.getElementById('back-to-history');
            if (backBtn) {
                backBtn.addEventListener('click', () => Router.navigate('/history'));
            }

            // 复制结论
            const copyBtn = document.getElementById('detail-copy-btn');
            if (copyBtn) {
                copyBtn.addEventListener('click', () => {
                    const verdictMap = { pass: '通过', revise: '需修改', reject: '拒绝发布', failed: '失败' };
                    const lines = [
                        `审核标题：${history.content_title || history.task_name || '未命名'}`,
                        `审核结论：${verdictMap[history.verdict] || history.verdict || '未知'}`,
                        `问题总数：${history.total_issues || 0}（严重 ${history.critical_issues || 0}）`,
                        '',
                        `摘要：${history.summary || '无'}`,
                    ];
                    navigator.clipboard.writeText(lines.join('\n')).then(() => {
                        UI.toast('已复制到剪贴板', 'success');
                    }).catch(() => {
                        UI.toast('复制失败，请手动复制', 'warning');
                    });
                });
            }

            // 导出 Markdown 报告
            const exportMdBtn = document.getElementById('detail-export-md-btn');
            if (exportMdBtn) {
                exportMdBtn.addEventListener('click', () => {
                    const merged = { ...history, issues: issues };
                    const md = this.resultToMarkdown(merged);
                    this._downloadFile(md, `审核报告_${history.review_id || history.id || Date.now()}.md`, 'text/markdown');
                    UI.toast('报告已导出', 'success');
                });
            }

            // 文本展开
            const toggleBtn = document.getElementById('toggle-detail-text');
            if (toggleBtn) {
                toggleBtn.addEventListener('click', () => {
                    const viewer = document.getElementById('detail-text-viewer');
                    viewer.style.display = viewer.style.display === 'none' ? 'block' : 'none';
                });
            }
        } catch (err) {
            App.updateContent(`
                <div class="empty-state">
                    <div class="empty-icon">⚠️</div>
                    <div class="mb-md">加载失败：${UI.escapeHtml(err.message)}</div>
                    <button class="btn btn-primary" onclick="Router.navigate('/history')">返回列表</button>
                </div>
            `);
        }
    },

    /* --------------------------------------------------------
     * 系统配置
     * -------------------------------------------------------- */
    async renderConfig() {
        App.updateContent(UI.skeleton(5));

        try {
            const [health, config] = await Promise.all([
                ApiClient.get('/health').catch(() => ({})),
                ApiClient.get('/config').catch(() => ({})),
            ]);

            const html = `
                <div class="d-flex align-items-center justify-content-between mb-lg">
                    <h2 style="font-size:20px;font-weight:600;">系统配置</h2>
                    <button class="btn btn-secondary btn-sm" onclick="location.reload()">刷新</button>
                </div>

                <!-- 系统状态 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">系统状态</span>
                    </div>
                    <div class="card-body">
                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label text-secondary">服务状态</label>
                                <div>
                                    <span class="status-dot ${health.status === 'ok' ? 'success' : 'danger'}"></span>
                                    ${health.status === 'ok' ? '运行中' : '异常'}
                                </div>
                            </div>
                            <div class="form-group">
                                <label class="form-label text-secondary">服务名称</label>
                                <div>${UI.escapeHtml(health.service || '-')}</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label text-secondary">版本</label>
                                <div>v${UI.escapeHtml(health.version || '-')}</div>
                            </div>
                        </div>
                        <div class="form-group">
                            <label class="form-label text-secondary">检查时间</label>
                            <div>${UI.formatDate(health.timestamp)}</div>
                        </div>
                    </div>
                </div>

                <!-- LLM 配置 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">LLM 配置</span>
                    </div>
                    <div class="card-body">
                        ${config.llm ? `
                            <div class="form-row">
                                <div class="form-group">
                                    <label class="form-label text-secondary">Provider</label>
                                    <div>${UI.escapeHtml(config.llm.provider || '-')}</div>
                                </div>
                                <div class="form-group">
                                    <label class="form-label text-secondary">模型</label>
                                    <div>${UI.escapeHtml(config.llm.model || '-')}</div>
                                </div>
                                <div class="form-group">
                                    <label class="form-label text-secondary">温度</label>
                                    <div>${config.llm.temperature ?? '-'}</div>
                                </div>
                            </div>
                            <div class="form-row">
                                <div class="form-group">
                                    <label class="form-label text-secondary">Base URL</label>
                                    <div class="text-sm text-mono">${UI.escapeHtml(config.llm.base_url || '-')}</div>
                                </div>
                                <div class="form-group">
                                    <label class="form-label text-secondary">最大 Token</label>
                                    <div>${config.llm.max_tokens ?? '-'}</div>
                                </div>
                                <div class="form-group">
                                    <label class="form-label text-secondary">超时（秒）</label>
                                    <div>${config.llm.timeout ?? '-'}</div>
                                </div>
                            </div>
                        ` : '<div class="text-secondary">无 LLM 配置</div>'}
                    </div>
                </div>

                <!-- 规则引擎配置 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">规则引擎配置</span>
                    </div>
                    <div class="card-body">
                        ${config.rule_engine ? UI.jsonViewer(config.rule_engine) : '<div class="text-secondary">无配置</div>'}
                    </div>
                </div>

                <!-- 爬虫配置 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">爬虫配置</span>
                    </div>
                    <div class="card-body">
                        ${config.crawler ? UI.jsonViewer(config.crawler) : '<div class="text-secondary">无配置</div>'}
                    </div>
                </div>

                <!-- 批量审核配置 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">批量审核配置</span>
                    </div>
                    <div class="card-body">
                        ${config.batch ? UI.jsonViewer(config.batch) : '<div class="text-secondary">无配置</div>'}
                    </div>
                </div>

                <!-- API 配置 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">API 配置</span>
                    </div>
                    <div class="card-body">
                        ${config.api ? UI.jsonViewer(config.api) : '<div class="text-secondary">无配置</div>'}
                    </div>
                </div>
            `;

            App.updateContent(html);
        } catch (err) {
            App.updateContent(`
                <div class="empty-state">
                    <div class="empty-icon">⚠️</div>
                    <div class="mb-md">加载失败：${UI.escapeHtml(err.message)}</div>
                    <button class="btn btn-primary" onclick="location.reload()">重新加载</button>
                </div>
            `);
        }
    },

    /* --------------------------------------------------------
     * 批量审核页面
     * -------------------------------------------------------- */
    async renderBatch() {
        const html = `
            <div class="d-flex align-items-center justify-content-between mb-lg">
                <h2 style="font-size:20px;font-weight:600;">批量审核</h2>
            </div>

            <!-- 提交批量审核 -->
            <div class="card">
                <div class="card-header">
                    <span class="card-title">提交批量审核</span>
                    <div class="tabs" style="display:flex;gap:8px;margin-left:auto;">
                        <button class="btn btn-sm btn-primary batch-tab active" data-tab="upload">文件上传</button>
                        <button class="btn btn-sm btn-secondary batch-tab" data-tab="link">链接导入</button>
                    </div>
                </div>
                <div class="card-body">
                    <!-- 文件上传模式 -->
                    <div id="batch-upload-panel">
                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label">任务名称（可选）</label>
                                <input type="text" id="batch-task-name" placeholder="批量审核任务">
                            </div>
                            <div class="form-group">
                                <label class="form-label">规则模板</label>
                                <select id="batch-rule-template">
                                    <option value="general">通用模板</option>
                                </select>
                            </div>
                        </div>

                        <div class="form-group">
                            <label class="form-label">提报表文件 <span class="required">*</span></label>
                            <div style="display:flex;gap:8px;align-items:center;">
                                <input type="file" id="batch-submission-file" accept=".xlsx,.xls,.json,.txt" style="display:none;">
                                <button class="btn btn-secondary btn-sm" id="choose-submission-btn">选择提报表</button>
                                <span id="submission-file-name" class="text-secondary text-sm">未选择</span>
                                <button class="btn btn-text btn-sm" id="clear-submission-btn" style="display:none;color:#dc2626;">清除</button>
                            </div>
                            <div class="form-hint">支持 xlsx/xls/json/txt 格式，所有待审文件共用此提报表</div>
                        </div>

                        <div class="form-group">
                            <label class="form-label">待审文件</label>
                            <div id="batch-drop-area" style="border:2px dashed #cbd5e1;border-radius:8px;padding:40px;text-align:center;background:#f8fafc;cursor:pointer;transition:all 0.2s;">
                                <div style="font-size:36px;margin-bottom:8px;">📁</div>
                                <div style="font-weight:500;margin-bottom:4px;">点击或拖拽文件到此处上传</div>
                                <div class="text-secondary text-sm">支持 txt / docx / pdf 格式，最多 100 个文件</div>
                                <input type="file" id="batch-content-files" multiple accept=".txt,.docx,.doc,.pdf" style="display:none;">
                            </div>
                        </div>

                        <div id="batch-file-list-area" style="display:none;">
                            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                                <div style="display:flex;gap:12px;align-items:center;">
                                    <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
                                        <input type="checkbox" id="batch-select-all" checked>
                                        <span class="text-sm">全选</span>
                                    </label>
                                    <span class="text-sm text-secondary">
                                        已选 <span id="batch-selected-count" style="color:#2563eb;font-weight:600;">0</span> / <span id="batch-total-count">0</span> 个文件
                                    </span>
                                </div>
                                <button class="btn btn-text btn-sm" id="batch-clear-files" style="color:#dc2626;">清空全部</button>
                            </div>
                            <div id="batch-file-list" style="max-height:300px;overflow-y:auto;border:1px solid #e2e8f0;border-radius:6px;"></div>
                        </div>

                        <div class="form-group">
                            <label class="form-label">官网 URL（可选，所有文件共用）</label>
                            <input type="text" id="batch-upload-official-urls" placeholder="多个 URL 用英文逗号分隔，如 https://a.com,https://b.com">
                        </div>

                        <div class="form-row" style="margin-top:16px;">
                            <div class="form-group" style="margin-bottom:0;">
                                <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
                                    <input type="checkbox" id="batch-crawl-option">
                                    <span>爬取官网信息</span>
                                </label>
                            </div>
                            <div class="form-group" style="margin-bottom:0;">
                                <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
                                    <input type="checkbox" id="batch-llm-option">
                                    <span>启用 LLM 语义审核</span>
                                </label>
                            </div>
                        </div>

                        <div style="margin-top:20px;padding-top:16px;border-top:1px solid #e2e8f0;display:flex;justify-content:flex-end;gap:8px;">
                            <button class="btn btn-primary" id="batch-upload-submit">
                                <span>开始批量审核</span>
                            </button>
                        </div>
                    </div>

                    <!-- 链接导入模式 -->
                    <div id="batch-link-panel" style="display:none;">
                        <div class="form-row">
                            <div class="form-group">
                                <label class="form-label">任务名称（可选）</label>
                                <input type="text" id="batch-link-task-name" placeholder="批量审核任务">
                            </div>
                            <div class="form-group">
                                <label class="form-label">规则模板</label>
                                <select id="batch-link-rule-template">
                                    <option value="general">通用模板</option>
                                </select>
                            </div>
                        </div>

                        <div class="form-group">
                            <label class="form-label">提报表文件 <span class="required">*</span></label>
                            <div style="display:flex;gap:8px;align-items:center;">
                                <input type="file" id="batch-link-submission-file" accept=".xlsx,.xls,.json,.txt" style="display:none;">
                                <button class="btn btn-secondary btn-sm" id="batch-choose-link-submission-btn">选择提报表</button>
                                <span id="batch-link-submission-file-name" class="text-secondary text-sm">未选择</span>
                                <button class="btn btn-text btn-sm" id="batch-clear-link-submission-btn" style="display:none;color:#dc2626;">清除</button>
                            </div>
                            <div class="form-hint">支持 xlsx/xls/json/txt 格式，所有链接共用此提报表</div>
                        </div>

                        <div class="form-group">
                            <label class="form-label">文档链接 <span class="required">*</span></label>
                            <textarea id="batch-document-urls" rows="8" placeholder="粘贴文档链接，每行一个或用分号(;)分隔，例如：&#10;https://xxx.feishu.cn/docx/aaa&#10;https://xxx.feishu.cn/docx/bbb&#10;https://xxx.feishu.cn/docx/ccc"></textarea>
                            <div class="form-hint">支持飞书文档链接和通用网页链接，每行一个或用分号分隔，最多 100 个</div>
                        </div>

                        <div class="form-group">
                            <label class="form-label">官网 URL（可选，所有文档共用）</label>
                            <input type="text" id="batch-link-official-urls" placeholder="多个 URL 用英文逗号分隔，如 https://a.com,https://b.com">
                        </div>

                        <div class="form-row" style="margin-top:16px;">
                            <div class="form-group" style="margin-bottom:0;">
                                <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
                                    <input type="checkbox" id="batch-link-crawl-option">
                                    <span>爬取官网信息</span>
                                </label>
                            </div>
                            <div class="form-group" style="margin-bottom:0;">
                                <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
                                    <input type="checkbox" id="batch-link-llm-option" checked>
                                    <span>启用 LLM 语义审核</span>
                                </label>
                            </div>
                        </div>

                        <div style="margin-top:20px;padding-top:16px;border-top:1px solid #e2e8f0;display:flex;justify-content:flex-end;gap:8px;">
                            <button class="btn btn-primary" id="batch-link-submit">
                                <span>开始批量审核</span>
                            </button>
                        </div>
                    </div>

                </div>
            </div>

            <!-- 进度查询 -->
            <div class="card">
                <div class="card-header">
                    <span class="card-title">查询批量任务</span>
                </div>
                <div class="card-body">
                    <div class="form-row">
                        <div class="form-group">
                            <label class="form-label">批量任务 ID</label>
                            <input type="text" id="batch-id-input" placeholder="输入批量任务 ID">
                        </div>
                        <div class="form-group d-flex align-items-end">
                            <button class="btn btn-primary" id="batch-query">查询进度</button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 批量结果区域 -->
            <div id="batch-result-area"></div>
        `;

        App.updateContent(html);
        this.bindBatchEvents();

        // 清除之前的追踪状态
        App._closeBatchWebSocket();
        if (App._batchTimer) {
            clearTimeout(App._batchTimer);
            App._batchTimer = null;
        }
        App._batchPollErrors = 0;
        App._batchPollInterval = 2000;

        // 恢复上次批量任务进度（切换页面后自动恢复）
        if (App._lastBatchId) {
            document.getElementById('batch-id-input').value = App._lastBatchId;
            // 异步查询进度并渲染
            ApiClient.get(`/review/batch/${App._lastBatchId}/progress`).then(progress => {
                if (progress.status === 'pending' || progress.status === 'processing') {
                    this.renderBatchProgress(progress);
                } else if (progress.status === 'completed') {
                    this.renderBatchProgress(progress);
                }
            }).catch(() => {
                // 任务已过期或不存在，清除记录
                App._lastBatchId = null;
            });
        }
    },

    /** 绑定批量审核事件 */
    bindBatchEvents() {
        const self = this;

        // Tab 切换（文件上传 / 链接导入）
        document.querySelectorAll('.batch-tab').forEach(tab => {
            tab.addEventListener('click', (e) => {
                const tabName = e.currentTarget.dataset.tab;
                document.querySelectorAll('.batch-tab').forEach(t => {
                    t.classList.remove('btn-primary');
                    t.classList.add('btn-secondary');
                });
                e.currentTarget.classList.remove('btn-secondary');
                e.currentTarget.classList.add('btn-primary');
                document.getElementById('batch-upload-panel').style.display = tabName === 'upload' ? 'block' : 'none';
                document.getElementById('batch-link-panel').style.display = tabName === 'link' ? 'block' : 'none';
            });
        });

        // 加载规则模板列表
        ApiClient.get('/rules/templates').then(res => {
            const templates = res.templates || [];
            ['batch-rule-template', 'batch-link-rule-template'].forEach(id => {
                const sel = document.getElementById(id);
                if (sel && templates.length > 0) {
                    sel.innerHTML = templates.map(t => `<option value="${UI.escapeHtml(t)}">${UI.escapeHtml(t)}</option>`).join('');
                }
            });
        }).catch(() => {});

        // 提报表文件选择
        const chooseSubBtn = document.getElementById('choose-submission-btn');
        const subFileInput = document.getElementById('batch-submission-file');
        if (chooseSubBtn && subFileInput) {
            chooseSubBtn.addEventListener('click', () => subFileInput.click());
            subFileInput.addEventListener('change', function() {
                if (this.files && this.files.length > 0) {
                    App._batchSubmissionFile = this.files[0];
                    document.getElementById('submission-file-name').textContent = this.files[0].name;
                    document.getElementById('clear-submission-btn').style.display = 'inline-block';
                }
            });
        }
        const clearSubBtn = document.getElementById('clear-submission-btn');
        if (clearSubBtn) {
            clearSubBtn.addEventListener('click', () => {
                App._batchSubmissionFile = null;
                document.getElementById('batch-submission-file').value = '';
                document.getElementById('submission-file-name').textContent = '未选择';
                clearSubBtn.style.display = 'none';
            });
        }

        // 待审文件上传 - 点击上传区
        const dropArea = document.getElementById('batch-drop-area');
        const contentFilesInput = document.getElementById('batch-content-files');
        if (dropArea && contentFilesInput) {
            dropArea.addEventListener('click', () => contentFilesInput.click());
            contentFilesInput.addEventListener('change', function() {
                if (this.files && this.files.length > 0) {
                    self._addBatchFiles(Array.from(this.files));
                }
                this.value = '';
            });

            // 拖拽上传
            dropArea.addEventListener('dragover', function(e) {
                e.preventDefault();
                this.style.borderColor = '#2563eb';
                this.style.background = '#eff6ff';
            });
            dropArea.addEventListener('dragleave', function(e) {
                e.preventDefault();
                this.style.borderColor = '#cbd5e1';
                this.style.background = '#f8fafc';
            });
            dropArea.addEventListener('drop', function(e) {
                e.preventDefault();
                this.style.borderColor = '#cbd5e1';
                this.style.background = '#f8fafc';
                if (e.dataTransfer && e.dataTransfer.files.length > 0) {
                    const files = Array.from(e.dataTransfer.files).filter(f => {
                        const ext = '.' + f.name.split('.').pop().toLowerCase();
                        return ['.txt', '.docx', '.doc', '.pdf'].includes(ext);
                    });
                    if (files.length > 0) {
                        self._addBatchFiles(files);
                    }
                }
            });
        }

        // 全选
        const selectAll = document.getElementById('batch-select-all');
        if (selectAll) {
            selectAll.addEventListener('change', function() {
                const checked = this.checked;
                App._batchFiles.forEach(f => f.selected = checked);
                document.querySelectorAll('.batch-file-checkbox').forEach(cb => cb.checked = checked);
                self._updateBatchFileStats();
            });
        }

        // 清空全部
        const clearAllBtn = document.getElementById('batch-clear-files');
        if (clearAllBtn) {
            clearAllBtn.addEventListener('click', () => {
                App._batchFiles = [];
                self._renderBatchFileList();
            });
        }

        // 提交批量文件上传
        const uploadSubmitBtn = document.getElementById('batch-upload-submit');
        if (uploadSubmitBtn) {
            uploadSubmitBtn.addEventListener('click', async function() {
                const selectedFiles = App._batchFiles.filter(f => f.selected);
                if (selectedFiles.length === 0) {
                    UI.toast('请至少选择一个待审文件', 'warning');
                    return;
                }

                if (!App._batchSubmissionFile) {
                    UI.toast('请选择提报表文件', 'warning');
                    return;
                }

                this.disabled = true;
                this.innerHTML = '<div class="spinner"></div> 提交中...';
                UI.loading(true, '正在提交批量审核...');

                try {
                    const formData = new FormData();
                    selectedFiles.forEach(f => formData.append('content_files', f.file));
                    formData.append('submission_file', App._batchSubmissionFile);

                    const taskName = document.getElementById('batch-task-name').value.trim();
                    if (taskName) formData.append('task_name', taskName);

                    const ruleTpl = document.getElementById('batch-rule-template').value;
                    formData.append('rule_template', ruleTpl);

                    const officialUrls = document.getElementById('batch-upload-official-urls').value.trim();
                    if (officialUrls) {
                        formData.append('official_urls', officialUrls);
                    }

                    formData.append('crawl_official_urls', document.getElementById('batch-crawl-option').checked);
                    formData.append('use_llm', document.getElementById('batch-llm-option').checked);

                    const progress = await ApiClient.post('/review/batch/upload', formData, true);
                    UI.toast(`批量任务已提交，ID: ${progress.batch_id}`, 'success');
                    App._lastBatchId = progress.batch_id;
                    document.getElementById('batch-id-input').value = progress.batch_id;
                    self.renderBatchProgress(progress);
                } catch (err) {
                    UI.toast(err.message || '提交失败', 'error');
                } finally {
                    this.disabled = false;
                    this.innerHTML = '<span>开始批量审核</span>';
                    UI.loading(false);
                }
            });
        }

        // 链接导入模式 — 提报表文件选择
        const linkChooseSubBtn = document.getElementById('batch-choose-link-submission-btn');
        const linkSubFileInput = document.getElementById('batch-link-submission-file');
        if (linkChooseSubBtn && linkSubFileInput) {
            linkChooseSubBtn.addEventListener('click', () => linkSubFileInput.click());
            linkSubFileInput.addEventListener('change', function() {
                if (this.files && this.files.length > 0) {
                    App._batchLinkSubmissionFile = this.files[0];
                    document.getElementById('batch-link-submission-file-name').textContent = this.files[0].name;
                    document.getElementById('batch-clear-link-submission-btn').style.display = 'inline-block';
                }
            });
        }
        const linkClearSubBtn = document.getElementById('batch-clear-link-submission-btn');
        if (linkClearSubBtn) {
            linkClearSubBtn.addEventListener('click', () => {
                App._batchLinkSubmissionFile = null;
                document.getElementById('batch-link-submission-file').value = '';
                document.getElementById('batch-link-submission-file-name').textContent = '未选择';
                linkClearSubBtn.style.display = 'none';
            });
        }

        // 链接导入模式 — 提交批量审核
        const linkSubmitBtn = document.getElementById('batch-link-submit');
        if (linkSubmitBtn) {
            linkSubmitBtn.addEventListener('click', async function() {
                const urlsText = document.getElementById('batch-document-urls').value.trim();
                if (!urlsText) {
                    UI.toast('请输入至少一个文档链接', 'warning');
                    return;
                }
                if (!App._batchLinkSubmissionFile) {
                    UI.toast('请选择提报表文件', 'warning');
                    return;
                }

                this.disabled = true;
                this.innerHTML = '<div class="spinner"></div> 抓取并提交中...';
                UI.loading(true, '正在抓取文档并提交批量审核...');

                try {
                    const formData = new FormData();
                    formData.append('document_urls', urlsText);
                    formData.append('submission_file', App._batchLinkSubmissionFile);

                    const taskName = document.getElementById('batch-link-task-name').value.trim();
                    if (taskName) formData.append('task_name', taskName);

                    const ruleTpl = document.getElementById('batch-link-rule-template').value;
                    formData.append('rule_template', ruleTpl);

                    const officialUrls = document.getElementById('batch-link-official-urls').value.trim();
                    if (officialUrls) formData.append('official_urls', officialUrls);

                    formData.append('crawl_official_urls', document.getElementById('batch-link-crawl-option').checked);
                    formData.append('use_llm', document.getElementById('batch-link-llm-option').checked);

                    const progress = await ApiClient.post('/review/batch/urls', formData, true);
                    if (progress.warnings && progress.warnings.length > 0) {
                        UI.toast(progress.warnings[0], 'warning');
                    }
                    UI.toast(`批量任务已提交，ID: ${progress.batch_id}`, 'success');
                    App._lastBatchId = progress.batch_id;
                    document.getElementById('batch-id-input').value = progress.batch_id;
                    self.renderBatchProgress(progress);
                } catch (err) {
                    UI.toast(err.message || '提交失败', 'error');
                } finally {
                    this.disabled = false;
                    this.innerHTML = '<span>开始批量审核</span>';
                    UI.loading(false);
                }
            });
        }

        // 查询进度
        const queryBtn = document.getElementById('batch-query');
        if (queryBtn) {
            queryBtn.addEventListener('click', async () => {
                const batchId = document.getElementById('batch-id-input').value.trim();
                if (!batchId) {
                    UI.toast('请输入批量任务 ID', 'warning');
                    return;
                }

                UI.loading(true, '查询中...');

                try {
                    const progress = await ApiClient.get(`/review/batch/${batchId}/progress`);
                    this.renderBatchProgress(progress);
                } catch (err) {
                    UI.toast(err.message || '查询失败', 'error');
                } finally {
                    UI.loading(false);
                }
            });
        }
    },

    /** 添加文件到批量列表 */
    _addBatchFiles(files) {
        if (!App._batchFiles) App._batchFiles = [];
        files.forEach(file => {
            // 检查是否已存在
            const exists = App._batchFiles.some(f => f.name === file.name && f.size === file.size);
            if (!exists && App._batchFiles.length < 100) {
                App._batchFiles.push({ file, name: file.name, size: file.size, selected: true, id: Date.now() + Math.random() });
            }
        });
        this._renderBatchFileList();
    },

    /** 渲染文件列表 */
    _renderBatchFileList() {
        const listArea = document.getElementById('batch-file-list-area');
        const listEl = document.getElementById('batch-file-list');
        if (!listArea || !listEl) return;

        const files = App._batchFiles || [];
        if (files.length === 0) {
            listArea.style.display = 'none';
            return;
        }

        listArea.style.display = 'block';
        let html = '<table style="width:100%;border-collapse:collapse;">';
        html += '<thead><tr style="background:#f1f5f9;border-bottom:1px solid #e2e8f0;">';
        html += '<th style="width:40px;padding:8px 12px;text-align:left;"></th>';
        html += '<th style="padding:8px 12px;text-align:left;font-size:13px;color:#475569;font-weight:500;">文件名</th>';
        html += '<th style="width:100px;padding:8px 12px;text-align:left;font-size:13px;color:#475569;font-weight:500;">大小</th>';
        html += '<th style="width:60px;padding:8px 12px;text-align:center;font-size:13px;color:#475569;font-weight:500;">操作</th>';
        html += '</tr></thead><tbody>';

        files.forEach((f, idx) => {
            const sizeStr = this._formatFileSize(f.size);
            html += `<tr style="border-bottom:1px solid #f1f5f9;">
                <td style="padding:8px 12px;"><input type="checkbox" class="batch-file-checkbox" data-idx="${idx}" ${f.selected ? 'checked' : ''} style="cursor:pointer;"></td>
                <td style="padding:8px 12px;font-size:13px;color:#1e293b;">
                    <span style="display:inline-flex;align-items:center;gap:6px;">
                        <span>📄</span>
                        <span>${UI.escapeHtml(f.name)}</span>
                    </span>
                </td>
                <td style="padding:8px 12px;font-size:13px;color:#64748b;">${sizeStr}</td>
                <td style="padding:8px 12px;text-align:center;">
                    <button class="btn btn-text btn-sm batch-file-del" data-idx="${idx}" style="color:#dc2626;padding:2px 6px;">删除</button>
                </td>
            </tr>`;
        });

        html += '</tbody></table>';
        listEl.innerHTML = html;

        // 绑定勾选事件
        document.querySelectorAll('.batch-file-checkbox').forEach(cb => {
            cb.addEventListener('change', function() {
                const idx = parseInt(this.dataset.idx);
                if (App._batchFiles[idx]) {
                    App._batchFiles[idx].selected = this.checked;
                    Pages._updateBatchFileStats();
                }
            });
        });

        // 绑定删除事件
        document.querySelectorAll('.batch-file-del').forEach(btn => {
            btn.addEventListener('click', function() {
                const idx = parseInt(this.dataset.idx);
                App._batchFiles.splice(idx, 1);
                Pages._renderBatchFileList();
            });
        });

        this._updateBatchFileStats();
    },

    /** 更新文件统计 */
    _updateBatchFileStats() {
        const files = App._batchFiles || [];
        const selected = files.filter(f => f.selected).length;
        const total = files.length;
        const selCountEl = document.getElementById('batch-selected-count');
        const totalCountEl = document.getElementById('batch-total-count');
        if (selCountEl) selCountEl.textContent = selected;
        if (totalCountEl) totalCountEl.textContent = total;

        const selectAll = document.getElementById('batch-select-all');
        if (selectAll) {
            selectAll.checked = total > 0 && selected === total;
        }
    },

    /** 格式化文件大小 */
    _formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    },

    /**
     * 渲染批量任务进度
     * @param {object} progress - 进度数据
     */
    renderBatchProgress(progress) {
        const area = document.getElementById('batch-result-area');
        if (!area) return;

        const statusMap = {
            pending: { cls: 'badge-default', text: '等待中' },
            processing: { cls: 'badge-info', text: '处理中' },
            completed: { cls: 'badge-success', text: '已完成' },
            failed: { cls: 'badge-danger', text: '失败' },
            cancelled: { cls: 'badge-default', text: '已取消' },
        };
        const statusInfo = statusMap[progress.status] || { cls: 'badge-default', text: progress.status || '未知' };

        const percent = progress.total > 0 ? Math.round((progress.completed / progress.total) * 100) : 0;
        const isActive = progress.status === 'pending' || progress.status === 'processing';

        const activeItemsHtml = (progress.active_items && progress.active_items.length > 0)
            ? `<div class="stat-item">处理中: <span class="stat-value">${progress.active_items.map(i => UI.escapeHtml(i)).join(', ')}</span></div>`
            : (progress.current_item ? `<div class="stat-item">当前: <span class="stat-value">${UI.escapeHtml(progress.current_item)}</span></div>` : '');

        const connStatus = App._batchWebSocket && App._batchWebSocket.readyState === WebSocket.OPEN
            ? '<span class="text-sm" style="color:#10b981;">实时连接</span>'
            : '<span class="text-sm text-secondary">轮询模式</span>';

        const html = `
            <div class="card">
                <div class="card-header">
                    <span class="card-title">批量任务进度</span>
                    <div class="card-actions">
                        ${connStatus}
                        <span class="badge ${statusInfo.cls}">${statusInfo.text}</span>
                        ${isActive ? `<button class="btn btn-danger btn-sm" id="batch-cancel">取消任务</button>` : ''}
                        ${progress.status === 'completed' ? `<button class="btn btn-primary btn-sm" id="batch-result">查看结果</button>` : ''}
                        ${isActive ? `<button class="btn btn-secondary btn-sm" id="batch-refresh">刷新</button>` : ''}
                    </div>
                </div>
                <div class="card-body">
                    <div class="batch-progress">
                        <div class="batch-progress-header">
                            <span class="text-bold">任务 ID: <code>${UI.escapeHtml(progress.batch_id)}</code></span>
                            <span class="text-sm text-secondary">${percent}%</span>
                        </div>
                        <div class="progress">
                            <div class="progress-bar ${progress.status === 'failed' ? 'danger' : progress.status === 'completed' ? 'success' : ''}" style="width:${percent}%;"></div>
                        </div>
                        <div class="batch-progress-stats">
                            <div class="stat-item">总计: <span class="stat-value">${progress.total}</span></div>
                            <div class="stat-item">已完成: <span class="stat-value">${progress.completed}</span></div>
                            <div class="stat-item text-success">成功: <span class="stat-value">${progress.success}</span></div>
                            <div class="stat-item text-danger">失败: <span class="stat-value">${progress.failed}</span></div>
                            ${activeItemsHtml}
                            ${progress.estimated_remaining_seconds != null ? `<div class="stat-item">预计剩余: <span class="stat-value">${Math.ceil(progress.estimated_remaining_seconds)}s</span></div>` : ''}
                        </div>
                    </div>

                    <div class="form-row mt-md">
                        <div class="form-group">
                            <label class="form-label text-secondary">开始时间</label>
                            <div>${UI.formatDate(progress.started_at)}</div>
                        </div>
                        ${progress.last_updated ? `<div class="form-group"><label class="form-label text-secondary">最后更新</label><div>${UI.formatDate(progress.last_updated)}</div></div>` : ''}
                    </div>
                </div>
            </div>
        `;

        area.innerHTML = html;

        const cancelBtn = document.getElementById('batch-cancel');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', async () => {
                const confirmed = await UI.confirm('确定要取消此批量任务吗？', { danger: true });
                if (!confirmed) return;

                try {
                    UI.loading(true, '正在取消...');
                    await ApiClient.post(`/review/batch/${progress.batch_id}/cancel`);
                    UI.toast('任务已取消', 'success');
                    const newProgress = await ApiClient.get(`/review/batch/${progress.batch_id}/progress`);
                    this.renderBatchProgress(newProgress);
                } catch (err) {
                    UI.toast(err.message || '取消失败', 'error');
                } finally {
                    UI.loading(false);
                }
            });
        }

        const refreshBtn = document.getElementById('batch-refresh');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', async () => {
                try {
                    const newProgress = await ApiClient.get(`/review/batch/${progress.batch_id}/progress`);
                    App._batchPollErrors = 0;
                    App._batchPollInterval = 2000;
                    this.renderBatchProgress(newProgress);
                } catch (err) {
                    UI.toast(err.message || '刷新失败', 'error');
                }
            });
        }

        const resultBtn = document.getElementById('batch-result');
        if (resultBtn) {
            resultBtn.addEventListener('click', async () => {
                try {
                    UI.loading(true, '加载结果...');
                    const result = await ApiClient.get(`/review/batch/${progress.batch_id}/result`);
                    this.renderBatchResult(result);
                } catch (err) {
                    UI.toast(err.message || '加载失败', 'error');
                } finally {
                    UI.loading(false);
                }
            });
        }

        if (isActive) {
            App._startBatchProgressTracking(progress.batch_id, (newProgress) => {
                this.renderBatchProgress(newProgress);
            });
        } else {
            App._closeBatchWebSocket();
            if (App._batchTimer) {
                clearTimeout(App._batchTimer);
                App._batchTimer = null;
            }
        }
    },

    /**
     * 渲染批量审核完整结果
     * @param {object} result - 批量结果
     */
    renderBatchResult(result) {
        const area = document.getElementById('batch-result-area');
        if (!area) return;

        const items = result.results || [];

        const html = `
            <div class="card">
                <div class="card-header">
                    <span class="card-title">批量审核结果</span>
                    <div class="card-actions">
                        <span class="badge badge-default">总计 ${result.total || 0}</span>
                        <span class="badge badge-success">成功 ${result.success || 0}</span>
                        <span class="badge badge-danger">失败 ${result.failed || 0}</span>
                    </div>
                </div>
                <div class="card-body">
                    <div class="form-group">
                        <label class="form-label text-secondary">汇总摘要</label>
                        <div style="padding:12px;background:var(--color-border-light);border-radius:6px;line-height:1.6;">
                            ${UI.escapeHtml(result.summary || '无摘要')}
                        </div>
                    </div>

                    ${result.processing_time != null ? `
                        <div class="text-sm text-secondary mb-md">
                            总处理时间: ${(result.processing_time).toFixed(1)}s
                            | 开始: ${UI.formatDate(result.started_at)}
                            | 完成: ${UI.formatDate(result.completed_at)}
                        </div>
                    ` : ''}

                    <h3 class="text-lg text-bold mb-md">各项结果</h3>
                    ${items.map((item, idx) => `
                        <div class="card" style="margin-bottom:12px;">
                            <div class="card-body">
                                <div class="d-flex align-items-center justify-content-between mb-sm">
                                    <div class="d-flex align-items-center gap-sm">
                                        <span class="text-bold">${idx + 1}. ${UI.escapeHtml(item.item_id || '未命名')}</span>
                                        ${item.verdict ? UI.badge(item.verdict) : ''}
                                        <span class="badge ${item.status === 'completed' ? 'badge-success' : item.status === 'failed' ? 'badge-danger' : 'badge-warning'}">
                                            ${item.status === 'completed' ? '完成' : item.status === 'failed' ? '失败' : '处理中'}
                                        </span>
                                    </div>
                                    ${item.processing_time != null ? `<span class="text-sm text-secondary">${item.processing_time.toFixed(1)}s</span>` : ''}
                                </div>
                                ${item.error ? `<div class="text-danger text-sm">${UI.escapeHtml(item.error)}</div>` : ''}
                                ${item.result ? `
                                    <div class="text-sm text-secondary mt-sm">摘要: ${UI.escapeHtml(item.result.summary || '-')}</div>
                                    <button class="btn btn-link btn-sm mt-sm" data-action="view-item" data-id="${item.review_id || ''}">查看详情</button>
                                ` : ''}
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;

        area.innerHTML = html;

        // 绑定查看详情事件
        area.querySelectorAll('[data-action="view-item"]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const id = e.currentTarget.dataset.id;
                if (id) {
                    Router.navigate(`/history/detail?id=${id}`);
                }
            });
        });
    },

    /* --------------------------------------------------------
     * 规则管理页面
     * -------------------------------------------------------- */
    async renderRulesManagement() {
        App.updateContent(UI.skeleton(4));

        try {
            const [ruleSetData, templates] = await Promise.all([
                ApiClient.get('/rules').catch(() => ({ meta: {}, rules_flat: [] })),
                ApiClient.get('/rules/templates').catch(() => ({ templates: [] })),
            ]);

            const meta = ruleSetData.meta || {};
            const rules = ruleSetData.rules_flat || [];

            const html = `
                <div class="d-flex align-items-center justify-content-between mb-lg" style="flex-wrap:wrap;gap:12px;">
                    <div>
                        <h2 style="font-size:20px;font-weight:600;">🛡️ 规则管理</h2>
                        <div class="text-sm text-secondary mt-sm">
                            ${UI.escapeHtml(meta.name || '当前规则集')}
                            ${meta.version ? `· v${UI.escapeHtml(meta.version)}` : ''}
                            ${meta.industry ? `· 行业: ${UI.escapeHtml(meta.industry)}` : ''}
                            ${meta.extends ? `· 继承: ${UI.escapeHtml(meta.extends)}` : ''}
                        </div>
                    </div>
                    <div class="d-flex gap-sm">
                        <button class="btn btn-secondary btn-sm" data-action="test-rule">
                            🧪 测试规则
                        </button>
                        <button class="btn btn-secondary btn-sm" data-action="add-rule">
                            ➕ 添加规则
                        </button>
                    </div>
                </div>

                <!-- 模板切换 -->
                <div class="card mb-lg">
                    <div class="card-body">
                        <div class="d-flex align-items-center gap-md" style="flex-wrap:wrap;">
                            <span class="text-bold">切换模板:</span>
                            ${(templates.templates || []).map(t => `
                                <button class="btn btn-sm ${t === meta.industry ? 'btn-primary' : 'btn-secondary'}"
                                        data-action="load-template" data-template="${UI.escapeHtml(t)}">
                                    ${UI.escapeHtml(t)}
                                </button>
                            `).join('')}
                        </div>
                    </div>
                </div>

                <!-- 规则统计 -->
                <div class="d-flex gap-md mb-lg" style="flex-wrap:wrap;">
                    <div class="card flex-1" style="min-width:150px;">
                        <div class="card-body">
                            <div class="text-sm text-secondary">总规则数</div>
                            <div class="text-xl text-bold text-primary">${rules.length}</div>
                        </div>
                    </div>
                    <div class="card flex-1" style="min-width:150px;">
                        <div class="card-body">
                            <div class="text-sm text-secondary">已启用</div>
                            <div class="text-xl text-bold text-success">${rules.filter(r => r.enabled !== false).length}</div>
                        </div>
                    </div>
                    <div class="card flex-1" style="min-width:150px;">
                        <div class="card-body">
                            <div class="text-sm text-secondary">已禁用</div>
                            <div class="text-xl text-bold text-secondary">${rules.filter(r => r.enabled === false).length}</div>
                        </div>
                    </div>
                    <div class="card flex-1" style="min-width:150px;">
                        <div class="card-body">
                            <div class="text-sm text-secondary">严重级别</div>
                            <div class="text-xl text-bold text-danger">${rules.filter(r => r.severity === 'critical').length}</div>
                        </div>
                    </div>
                </div>

                <!-- 规则列表 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">规则列表</span>
                    </div>
                    <div class="card-body p-0">
                        ${rules.length === 0 ? UI.emptyState('暂无规则', '🛡️') : `
                            <div class="table-wrapper" style="border:none;border-radius:0;">
                                <table>
                                    <thead>
                                        <tr>
                                            <th style="width:120px;">类型</th>
                                            <th>规则</th>
                                            <th style="width:80px;">严重</th>
                                            <th style="width:60px;">权重</th>
                                            <th style="width:80px;">状态</th>
                                            <th style="width:150px;">操作</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${rules.map(r => `
                                            <tr>
                                                <td><span class="badge badge-info">${UI.escapeHtml(r.type_display || r.type || '-')}</span></td>
                                                <td>
                                                    <div class="text-bold">${UI.escapeHtml(r.pattern || r.description || r.id)}</div>
                                                    ${r.description ? `<div class="text-sm text-secondary">${UI.escapeHtml(r.description)}</div>` : ''}
                                                    ${r.category ? `<div class="text-sm text-secondary">分类: ${UI.escapeHtml(r.category)}</div>` : ''}
                                                </td>
                                                <td>${UI.severityBadge(r.severity)}</td>
                                                <td>${r.weight || 100}</td>
                                                <td>
                                                    <span class="badge ${r.enabled !== false ? 'badge-success' : 'badge-default'}">
                                                        ${r.enabled !== false ? '启用' : '禁用'}
                                                    </span>
                                                </td>
                                                <td>
                                                    <button class="btn btn-link btn-sm" data-action="toggle-rule" data-id="${UI.escapeHtml(r.id)}">
                                                        ${r.enabled !== false ? '禁用' : '启用'}
                                                    </button>
                                                    <button class="btn btn-link btn-sm text-danger" data-action="delete-rule" data-id="${UI.escapeHtml(r.id)}">
                                                        删除
                                                    </button>
                                                </td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            </div>
                        `}
                    </div>
                </div>
            `;

            App.updateContent(html);

            // 绑定事件
            document.querySelectorAll('[data-action="load-template"]').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const t = btn.dataset.template;
                    UI.toast(`加载模板: ${t}（需重启服务生效）`, 'info');
                });
            });

            document.querySelectorAll('[data-action="toggle-rule"]').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const id = btn.dataset.id;
                    const rule = rules.find(r => r.id === id);
                    if (!rule) return;
                    try {
                        await ApiClient.request('PATCH', '/rules/' + id, { body: { enabled: !rule.enabled } });
                        UI.toast(rule.enabled ? '规则已禁用' : '规则已启用', 'success');
                        this.renderRulesManagement();
                    } catch (err) {
                        UI.toast(err.message || '操作失败', 'error');
                    }
                });
            });

            document.querySelectorAll('[data-action="delete-rule"]').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const id = btn.dataset.id;
                    const ok = await UI.confirm(`确定要删除规则 "${id}" 吗？`, { danger: true, confirmText: '删除' });
                    if (!ok) return;
                    try {
                        await ApiClient.delete('/rules/' + id);
                        UI.toast('规则已删除', 'success');
                        this.renderRulesManagement();
                    } catch (err) {
                        UI.toast(err.message || '删除失败', 'error');
                    }
                });
            });

            document.querySelector('[data-action="test-rule"]')?.addEventListener('click', () => {
                this._showRuleTestDialog();
            });

            document.querySelector('[data-action="add-rule"]')?.addEventListener('click', () => {
                this._showAddRuleDialog();
            });
        } catch (err) {
            App.updateContent(`
                <div class="empty-state">
                    <div class="empty-icon">⚠️</div>
                    <div class="mb-md">加载失败: ${UI.escapeHtml(err.message)}</div>
                </div>
            `);
        }
    },

    _showRuleTestDialog() {
        const html = `
            <div class="form-group">
                <label class="form-label">待测试正文</label>
                <textarea id="rule-test-content" class="form-textarea" rows="6" placeholder="输入要测试的正文内容..."></textarea>
            </div>
            <div class="form-group">
                <label class="form-label">规则（JSON 格式）</label>
                <textarea id="rule-test-rule" class="form-textarea" rows="6" placeholder='{"pattern":"测试词","severity":"major","is_regex":false,"type":"forbidden_claims"}'></textarea>
            </div>
            <div id="rule-test-result" class="mt-md"></div>
        `;
        const footer = `
            <button class="btn btn-secondary" data-action="cancel">取消</button>
            <button class="btn btn-primary" data-action="run-test">运行测试</button>
        `;
        const modal = UI.modal('🧪 规则测试', html, { size: 'md', footer });
        modal.querySelector('[data-action="cancel"]').addEventListener('click', () => UI.closeModal());
        modal.querySelector('[data-action="run-test"]').addEventListener('click', async () => {
            const content = modal.querySelector('#rule-test-content').value;
            const ruleStr = modal.querySelector('#rule-test-rule').value;
            const resultEl = modal.querySelector('#rule-test-result');
            if (!content || !ruleStr) {
                UI.toast('请填写正文和规则', 'warning');
                return;
            }
            try {
                const rule = JSON.parse(ruleStr);
                const result = await ApiClient.post('/rules/test', { content, rule_data: rule });
                resultEl.innerHTML = `
                    <div class="alert ${result.matched ? 'alert-warning' : 'alert-success'}">
                        <strong>${result.matched ? '✓ 规则匹配' : '○ 未匹配'}</strong>
                        · 匹配数: ${result.match_count}
                    </div>
                    ${result.issues && result.issues.length > 0 ? `
                        <div class="mt-sm">
                            ${result.issues.map(i => `<div class="text-sm">· ${UI.escapeHtml(i.title || '')}</div>`).join('')}
                        </div>
                    ` : ''}
                `;
            } catch (e) {
                resultEl.innerHTML = `<div class="alert alert-danger">测试失败: ${UI.escapeHtml(e.message)}</div>`;
            }
        });
    },

    _showAddRuleDialog() {
        const html = `
            <div class="form-group">
                <label class="form-label">规则类型</label>
                <select id="new-rule-type" class="form-select">
                    <option value="forbidden_claims">禁用词</option>
                    <option value="must_not_mention">禁止提及</option>
                    <option value="exaggeration_patterns">夸大表述</option>
                    <option value="composite_rules">复合条件</option>
                </select>
            </div>
            <div class="form-group">
                <label class="form-label">匹配模式</label>
                <input id="new-rule-pattern" class="form-input" placeholder="例如: 第一名 / 100% / 根治" />
            </div>
            <div class="form-group">
                <label class="form-label">严重程度</label>
                <select id="new-rule-severity" class="form-select">
                    <option value="critical">严重</option>
                    <option value="major" selected>重要</option>
                    <option value="minor">次要</option>
                    <option value="info">提示</option>
                </select>
            </div>
            <div class="form-group">
                <label class="form-label">描述（可选）</label>
                <input id="new-rule-desc" class="form-input" placeholder="规则说明" />
            </div>
            <div class="form-group">
                <label class="form-label">
                    <input type="checkbox" id="new-rule-regex" />
                    作为正则表达式
                </label>
            </div>
        `;
        const footer = `
            <button class="btn btn-secondary" data-action="cancel">取消</button>
            <button class="btn btn-primary" data-action="save">保存</button>
        `;
        const modal = UI.modal('➕ 添加规则', html, { size: 'md', footer });
        modal.querySelector('[data-action="cancel"]').addEventListener('click', () => UI.closeModal());
        modal.querySelector('[data-action="save"]').addEventListener('click', async () => {
            const ruleType = modal.querySelector('#new-rule-type').value;
            const pattern = modal.querySelector('#new-rule-pattern').value.trim();
            const severity = modal.querySelector('#new-rule-severity').value;
            const desc = modal.querySelector('#new-rule-desc').value.trim();
            const isRegex = modal.querySelector('#new-rule-regex').checked;
            if (!pattern) {
                UI.toast('请填写匹配模式', 'warning');
                return;
            }
            const ruleData = { pattern, severity, enabled: true, weight: 100 };
            if (desc) ruleData.description = desc;
            if (ruleType === 'forbidden_claims' || ruleType === 'must_not_mention') {
                ruleData.is_regex = isRegex;
            }
            try {
                await ApiClient.request('POST', '/rules/add', { body: ruleData, params: { rule_type: ruleType } });
                UI.toast('规则已添加', 'success');
                UI.closeModal();
                this.renderRulesManagement();
            } catch (e) {
                UI.toast(e.message || '添加失败', 'error');
            }
        });
    },

    /* --------------------------------------------------------
     * 流程管理页面
     * -------------------------------------------------------- */
    async renderWorkflowDashboard() {
        App.updateContent(UI.skeleton(4));

        try {
            const [countsRes, recentRes] = await Promise.all([
                ApiClient.get('/workflow/status/counts').catch(() => ({ total: 0, counts: {} })),
                ApiClient.get('/history', { page: 1, page_size: 20 }).catch(() => ({ data: [], total: 0 })),
            ]);

            const counts = countsRes.counts || {};
            const total = countsRes.total || 0;
            const statusDisplay = countsRes.status_display || {};

            const statusColors = {
                pending: 'badge-default',
                completed: 'badge-info',
                reviewing: 'badge-warning',
                approved: 'badge-success',
                rejected: 'badge-danger',
                revising: 'badge-warning',
                archived: 'badge-secondary',
                failed: 'badge-danger',
                processing: 'badge-info',
            };

            const html = `
                <div class="d-flex align-items-center justify-content-between mb-lg">
                    <h2 style="font-size:20px;font-weight:600;">🔄 流程管理</h2>
                    <button class="btn btn-secondary btn-sm" data-action="refresh">
                        🔃 刷新
                    </button>
                </div>

                <!-- 状态统计 -->
                <div class="card mb-lg">
                    <div class="card-header">
                        <span class="card-title">流程状态分布</span>
                        <span class="badge badge-primary">总计 ${total}</span>
                    </div>
                    <div class="card-body">
                        ${Object.keys(counts).length === 0 ? UI.emptyState('暂无流程记录', '🔄') : `
                            <div class="d-flex gap-md" style="flex-wrap:wrap;">
                                ${Object.entries(counts).map(([status, count]) => `
                                    <div class="card flex-1" style="min-width:140px;cursor:pointer;" data-action="filter-status" data-status="${status}">
                                        <div class="card-body" style="text-align:center;">
                                            <span class="badge ${statusColors[status] || 'badge-default'}">${UI.escapeHtml(statusDisplay[status] || status)}</span>
                                            <div class="text-xl text-bold mt-sm">${count}</div>
                                            <div class="text-sm text-secondary">${total > 0 ? (count / total * 100).toFixed(1) : 0}%</div>
                                        </div>
                                    </div>
                                `).join('')}
                            </div>
                        `}
                    </div>
                </div>

                <!-- 最近审核流程 -->
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">最近审核记录</span>
                        <a href="#/history" class="btn btn-link btn-sm">查看全部 →</a>
                    </div>
                    <div class="card-body p-0">
                        ${(recentRes.data || []).length === 0 ? UI.emptyState('暂无审核记录', '📋') : `
                            <div class="table-wrapper" style="border:none;border-radius:0;">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>任务名称</th>
                                            <th>公司</th>
                                            <th>结论</th>
                                            <th>流程状态</th>
                                            <th>时间</th>
                                            <th>操作</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${recentRes.data.map(r => `
                                            <tr data-id="${r.review_id || r.id}">
                                                <td>${UI.escapeHtml(r.task_name || '-')}</td>
                                                <td>${UI.escapeHtml(r.company_name || '-')}</td>
                                                <td>${UI.badge(r.verdict)}</td>
                                                <td><span class="badge ${statusColors[r.workflow_status] || 'badge-default'}">${UI.escapeHtml(statusDisplay[r.workflow_status] || r.workflow_status || '未开始')}</span></td>
                                                <td class="text-sm text-secondary">${UI.formatDate(r.reviewed_at)}</td>
                                                <td>
                                                    <button class="btn btn-link btn-sm" data-action="view-workflow" data-id="${r.review_id || r.id}">流程</button>
                                                </td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            </div>
                        `}
                    </div>
                </div>
            `;

            App.updateContent(html);

            // 绑定事件
            document.querySelector('[data-action="refresh"]')?.addEventListener('click', () => this.renderWorkflowDashboard());
            document.querySelectorAll('[data-action="view-workflow"]').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    Router.navigate(`/workflow/detail?id=${btn.dataset.id}`);
                });
            });
            document.querySelectorAll('[data-action="filter-status"]').forEach(el => {
                el.addEventListener('click', () => {
                    const s = el.dataset.status;
                    Router.navigate(`/history?workflow_status=${s}`);
                });
            });
        } catch (err) {
            App.updateContent(`
                <div class="empty-state">
                    <div class="empty-icon">⚠️</div>
                    <div class="mb-md">加载失败: ${UI.escapeHtml(err.message)}</div>
                </div>
            `);
        }
    },

    async renderWorkflowDetail(reviewId) {
        if (!reviewId) {
            App.updateContent(UI.emptyState('请提供审核记录 ID', '⚠️'));
            return;
        }
        App.updateContent(UI.skeleton(4));
        try {
            const [summary, comments] = await Promise.all([
                ApiClient.get('/workflow/' + reviewId).catch(() => null),
                ApiClient.get('/workflow/' + reviewId + '/comments').catch(() => ({ data: [] })),
            ]);
            if (!summary) {
                App.updateContent(UI.emptyState('该审核记录暂无流程信息', '🔄'));
                return;
            }

            const user = Auth.getUser();
            const isAdmin = user && user.role === 'admin';

            const html = `
                <div class="d-flex align-items-center justify-content-between mb-lg">
                    <div>
                        <a href="#/workflow" class="btn btn-link btn-sm">← 返回</a>
                        <h2 style="font-size:18px;font-weight:600;display:inline;margin-left:8px;">
                            流程详情 · ${UI.escapeHtml(reviewId.substring(0, 8))}...
                        </h2>
                    </div>
                </div>

                <div class="card mb-lg">
                    <div class="card-header">
                        <span class="card-title">当前状态</span>
                        <span class="badge badge-primary">${UI.escapeHtml(summary.status_display || summary.current_status)}</span>
                    </div>
                    <div class="card-body">
                        <div class="d-flex gap-sm" style="flex-wrap:wrap;">
                            ${summary.can_review ? '<button class="btn btn-warning btn-sm" data-action="wf-transition" data-act="review">提交复核</button>' : ''}
                            ${summary.can_approve ? '<button class="btn btn-success btn-sm" data-action="wf-transition" data-act="approve">✓ 复核通过</button>' : ''}
                            ${summary.can_reject ? '<button class="btn btn-danger btn-sm" data-action="wf-transition" data-act="reject">✕ 复核不通过</button>' : ''}
                            ${summary.can_revise ? '<button class="btn btn-secondary btn-sm" data-action="wf-transition" data-act="revise">↩ 返回修改</button>' : ''}
                            ${summary.can_archive ? '<button class="btn btn-secondary btn-sm" data-action="wf-transition" data-act="archive">📦 归档</button>' : ''}
                        </div>
                        ${!isAdmin && (summary.can_approve || summary.can_reject) ? '<div class="text-sm text-warning mt-sm">⚠️ 审批操作需要管理员权限</div>' : ''}
                    </div>
                </div>

                <div class="card mb-lg">
                    <div class="card-header">
                        <span class="card-title">审核意见（${summary.comments_count}）</span>
                        <button class="btn btn-link btn-sm" data-action="add-comment">+ 添加意见</button>
                    </div>
                    <div class="card-body">
                        ${(comments.data || []).length === 0 ? UI.emptyState('暂无审核意见', '💬') : `
                            ${comments.data.map(c => `
                                <div class="card" style="margin-bottom:8px;background:var(--color-border-light);">
                                    <div class="card-body">
                                        <div class="d-flex align-items-center justify-content-between mb-sm">
                                            <div>
                                                <strong>${UI.escapeHtml(c.author)}</strong>
                                                <span class="badge badge-default">${UI.escapeHtml(c.author_role)}</span>
                                            </div>
                                            <span class="text-sm text-secondary">${UI.formatDate(c.created_at)}</span>
                                        </div>
                                        <div>${UI.escapeHtml(c.content)}</div>
                                    </div>
                                </div>
                            `).join('')}
                        `}
                    </div>
                </div>

                <div class="card">
                    <div class="card-header">
                        <span class="card-title">审计日志</span>
                    </div>
                    <div class="card-body p-0">
                        ${(summary.audit_logs || []).length === 0 ? UI.emptyState('暂无日志', '📜') : `
                            <div class="table-wrapper" style="border:none;border-radius:0;">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>时间</th>
                                            <th>操作</th>
                                            <th>状态变更</th>
                                            <th>操作人</th>
                                            <th>备注</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${summary.audit_logs.map(log => `
                                            <tr>
                                                <td class="text-sm">${UI.formatDate(log.created_at)}</td>
                                                <td><span class="badge badge-info">${UI.escapeHtml(log.action)}</span></td>
                                                <td class="text-sm">${UI.escapeHtml(log.from_status || '-')} → ${UI.escapeHtml(log.to_status || '-')}</td>
                                                <td>${UI.escapeHtml(log.operator || '系统')}</td>
                                                <td class="text-sm">${UI.escapeHtml(log.note || '-')}</td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            </div>
                        `}
                    </div>
                </div>
            `;

            App.updateContent(html);

            // 状态流转
            document.querySelectorAll('[data-action="wf-transition"]').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const act = btn.dataset.act;
                    const note = prompt(`操作备注（${act}）:`) || '';
                    try {
                        await ApiClient.post(`/workflow/${reviewId}/transition`, { action: act, note });
                        UI.toast('操作成功', 'success');
                        this.renderWorkflowDetail(reviewId);
                    } catch (e) {
                        UI.toast(e.message || '操作失败', 'error');
                    }
                });
            });

            // 添加意见
            document.querySelector('[data-action="add-comment"]')?.addEventListener('click', () => {
                this._showAddCommentDialog(reviewId);
            });
        } catch (err) {
            App.updateContent(`
                <div class="empty-state">
                    <div class="empty-icon">⚠️</div>
                    <div class="mb-md">加载失败: ${UI.escapeHtml(err.message)}</div>
                </div>
            `);
        }
    },

    _showAddCommentDialog(reviewId) {
        const html = `
            <div class="form-group">
                <label class="form-label">意见内容</label>
                <textarea id="comment-content" class="form-textarea" rows="5" placeholder="请输入审核意见..."></textarea>
            </div>
        `;
        const footer = `
            <button class="btn btn-secondary" data-action="cancel">取消</button>
            <button class="btn btn-primary" data-action="save">提交</button>
        `;
        const modal = UI.modal('💬 添加审核意见', html, { size: 'md', footer });
        modal.querySelector('[data-action="cancel"]').addEventListener('click', () => UI.closeModal());
        modal.querySelector('[data-action="save"]').addEventListener('click', async () => {
            const content = modal.querySelector('#comment-content').value.trim();
            if (!content) {
                UI.toast('请填写意见内容', 'warning');
                return;
            }
            try {
                await ApiClient.post(`/workflow/${reviewId}/comments`, { content });
                UI.toast('意见已提交', 'success');
                UI.closeModal();
                this.renderWorkflowDetail(reviewId);
            } catch (e) {
                UI.toast(e.message || '提交失败', 'error');
            }
        });
    },

    /* --------------------------------------------------------
     * 系统监控页面
     * -------------------------------------------------------- */
    async renderMonitoring() {
        App.updateContent(UI.skeleton(4));
        try {
            const [health, metrics] = await Promise.all([
                ApiClient.get('/health'),
                ApiClient.get('/metrics').catch(() => null),
            ]);

            const api = (metrics && metrics.api) || {};
            const review = (metrics && metrics.review) || {};
            const endpoints = (metrics && metrics.endpoints) || {};
            const llm = (health && health.llm) || {};
            const wf = (health && health.workflow) || {};
            const db = (health && health.database) || {};

            const html = `
                <div class="d-flex align-items-center justify-content-between mb-lg">
                    <h2 style="font-size:20px;font-weight:600;">📈 系统监控</h2>
                    <button class="btn btn-secondary btn-sm" data-action="refresh">🔃 刷新</button>
                </div>

                <!-- 组件状态 -->
                <div class="card mb-lg">
                    <div class="card-header">
                        <span class="card-title">组件健康状态</span>
                    </div>
                    <div class="card-body">
                        <div class="d-flex gap-md" style="flex-wrap:wrap;">
                            <div class="card flex-1" style="min-width:180px;">
                                <div class="card-body">
                                    <div class="text-sm text-secondary">数据库</div>
                                    <div class="d-flex align-items-center gap-sm mt-sm">
                                        <span style="width:8px;height:8px;border-radius:50%;background:${db.status === 'ok' ? 'var(--color-success)' : 'var(--color-danger)'};"></span>
                                        <strong>${db.status === 'ok' ? '正常' : '异常'}</strong>
                                    </div>
                                </div>
                            </div>
                            <div class="card flex-1" style="min-width:180px;">
                                <div class="card-body">
                                    <div class="text-sm text-secondary">LLM 服务</div>
                                    <div class="d-flex align-items-center gap-sm mt-sm">
                                        <span style="width:8px;height:8px;border-radius:50%;background:${llm.status === 'ok' ? 'var(--color-success)' : 'var(--color-warning)'};"></span>
                                        <strong>${llm.status === 'ok' ? '正常' : (llm.status || '未知')}</strong>
                                    </div>
                                    <div class="text-sm text-secondary mt-sm">${UI.escapeHtml(llm.provider || '-')} / ${UI.escapeHtml(llm.model || '-')}</div>
                                </div>
                            </div>
                            <div class="card flex-1" style="min-width:180px;">
                                <div class="card-body">
                                    <div class="text-sm text-secondary">流程引擎</div>
                                    <div class="d-flex align-items-center gap-sm mt-sm">
                                        <span style="width:8px;height:8px;border-radius:50%;background:var(--color-success);"></span>
                                        <strong>正常</strong>
                                    </div>
                                    <div class="text-sm text-secondary mt-sm">${wf.total_records || 0} 条记录</div>
                                </div>
                            </div>
                            <div class="card flex-1" style="min-width:180px;">
                                <div class="card-body">
                                    <div class="text-sm text-secondary">整体状态</div>
                                    <div class="d-flex align-items-center gap-sm mt-sm">
                                        <span style="width:8px;height:8px;border-radius:50%;background:${health.status === 'ok' ? 'var(--color-success)' : 'var(--color-warning)'};"></span>
                                        <strong>${health.status === 'ok' ? '运行正常' : '降级运行'}</strong>
                                    </div>
                                    <div class="text-sm text-secondary mt-sm">运行 ${metrics ? Math.round(metrics.uptime_seconds || 0) : 0} 秒</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- API 性能 -->
                <div class="card mb-lg">
                    <div class="card-header">
                        <span class="card-title">API 性能</span>
                    </div>
                    <div class="card-body">
                        <div class="d-flex gap-md mb-md" style="flex-wrap:wrap;">
                            <div class="card flex-1" style="min-width:140px;">
                                <div class="card-body" style="text-align:center;">
                                    <div class="text-sm text-secondary">总请求数</div>
                                    <div class="text-xl text-bold text-primary">${api.total_requests || 0}</div>
                                </div>
                            </div>
                            <div class="card flex-1" style="min-width:140px;">
                                <div class="card-body" style="text-align:center;">
                                    <div class="text-sm text-secondary">错误数</div>
                                    <div class="text-xl text-bold text-danger">${api.total_errors || 0}</div>
                                </div>
                            </div>
                            <div class="card flex-1" style="min-width:140px;">
                                <div class="card-body" style="text-align:center;">
                                    <div class="text-sm text-secondary">错误率</div>
                                    <div class="text-xl text-bold ${(api.error_rate_percent || 0) > 5 ? 'text-danger' : 'text-success'}">${(api.error_rate_percent || 0).toFixed(2)}%</div>
                                </div>
                            </div>
                            <div class="card flex-1" style="min-width:140px;">
                                <div class="card-body" style="text-align:center;">
                                    <div class="text-sm text-secondary">平均耗时</div>
                                    <div class="text-xl text-bold">${(api.avg_response_time_ms || 0).toFixed(0)}ms</div>
                                </div>
                            </div>
                            <div class="card flex-1" style="min-width:140px;">
                                <div class="card-body" style="text-align:center;">
                                    <div class="text-sm text-secondary">P95 耗时</div>
                                    <div class="text-xl text-bold">${(api.p95_response_time_ms || 0).toFixed(0)}ms</div>
                                </div>
                            </div>
                        </div>

                        ${Object.keys(endpoints).length > 0 ? `
                            <h4 class="text-bold mb-sm">端点统计</h4>
                            <div class="table-wrapper">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>端点</th>
                                            <th>调用数</th>
                                            <th>错误数</th>
                                            <th>错误率</th>
                                            <th>平均耗时</th>
                                            <th>最大耗时</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${Object.entries(endpoints).map(([ep, stat]) => `
                                            <tr>
                                                <td class="text-sm">${UI.escapeHtml(ep)}</td>
                                                <td>${stat.count}</td>
                                                <td>${stat.errors}</td>
                                                <td>${(stat.error_rate || 0).toFixed(1)}%</td>
                                                <td>${(stat.avg_duration_ms || 0).toFixed(0)}ms</td>
                                                <td>${(stat.max_duration_ms || 0).toFixed(0)}ms</td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            </div>
                        ` : ''}
                    </div>
                </div>

                <!-- 审核质量 -->
                <div class="card mb-lg">
                    <div class="card-header">
                        <span class="card-title">审核质量</span>
                    </div>
                    <div class="card-body">
                        <div class="d-flex gap-md" style="flex-wrap:wrap;">
                            <div class="card flex-1" style="min-width:140px;">
                                <div class="card-body" style="text-align:center;">
                                    <div class="text-sm text-secondary">总审核数</div>
                                    <div class="text-xl text-bold text-primary">${review.total_reviews || 0}</div>
                                </div>
                            </div>
                            <div class="card flex-1" style="min-width:140px;">
                                <div class="card-body" style="text-align:center;">
                                    <div class="text-sm text-secondary">LLM 审核</div>
                                    <div class="text-xl text-bold">${review.llm_reviews || 0}</div>
                                </div>
                            </div>
                            <div class="card flex-1" style="min-width:140px;">
                                <div class="card-body" style="text-align:center;">
                                    <div class="text-sm text-secondary">LLM 成功率</div>
                                    <div class="text-xl text-bold ${(review.llm_success_rate_percent || 100) < 90 ? 'text-danger' : 'text-success'}">${(review.llm_success_rate_percent || 100).toFixed(1)}%</div>
                                </div>
                            </div>
                            <div class="card flex-1" style="min-width:140px;">
                                <div class="card-body" style="text-align:center;">
                                    <div class="text-sm text-secondary">平均问题数</div>
                                    <div class="text-xl text-bold">${(review.avg_issues_per_review || 0).toFixed(1)}</div>
                                </div>
                            </div>
                            <div class="card flex-1" style="min-width:140px;">
                                <div class="card-body" style="text-align:center;">
                                    <div class="text-sm text-secondary">平均审核耗时</div>
                                    <div class="text-xl text-bold">${(review.avg_review_duration_ms || 0).toFixed(0)}ms</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- LLM 统计 -->
                ${llm.stats ? `
                    <div class="card">
                        <div class="card-header">
                            <span class="card-title">LLM 调用统计</span>
                        </div>
                        <div class="card-body">
                            <div class="d-flex gap-md" style="flex-wrap:wrap;">
                                <div class="text-sm">总调用: <strong>${llm.stats.total_calls || 0}</strong></div>
                                <div class="text-sm">成功: <strong class="text-success">${llm.stats.success_calls || 0}</strong></div>
                                <div class="text-sm">重试: <strong class="text-warning">${llm.stats.retry_calls || 0}</strong></div>
                                <div class="text-sm">失败: <strong class="text-danger">${llm.stats.failed_calls || 0}</strong></div>
                                <div class="text-sm">总耗时: <strong>${(llm.stats.total_duration || 0).toFixed(1)}s</strong></div>
                            </div>
                        </div>
                    </div>
                ` : ''}
            `;

            App.updateContent(html);
            document.querySelector('[data-action="refresh"]')?.addEventListener('click', () => this.renderMonitoring());
        } catch (err) {
            App.updateContent(`
                <div class="empty-state">
                    <div class="empty-icon">⚠️</div>
                    <div class="mb-md">加载失败: ${UI.escapeHtml(err.message)}（监控接口需要管理员权限）</div>
                </div>
            `);
        }
    },
};

/* ============================================================
 * 6. 路由系统 — 基于 hash 的 SPA 路由
 * ============================================================ */
const Router = {
    /** 路由表 */
    routes: {
        '/login': { handler: () => App.showLogin(), authRequired: false, nav: null },
        '/dashboard': { handler: () => App.showPage(() => Pages.renderDashboard()), authRequired: true, nav: 'dashboard' },
        '/review': { handler: () => App.showPage(() => Pages.renderReview()), authRequired: true, nav: 'review' },
        '/review/result': { handler: () => App.showPage(() => Pages.renderReviewResult(App.lastReviewResult || {})), authRequired: true, nav: 'review' },
        '/history': { handler: () => App.showPage(() => Pages.renderHistory(1, {})), authRequired: true, nav: 'history' },
        '/history/detail': { handler: () => App.showPage(() => {
            const hash = Router.parseHash();
            Pages.renderHistoryDetail(hash.query.id);
        }), authRequired: true, nav: 'history' },
        '/rules': { handler: () => App.showPage(() => Pages.renderRulesManagement()), authRequired: true, nav: 'rules' },
        '/workflow': { handler: () => App.showPage(() => Pages.renderWorkflowDashboard()), authRequired: true, nav: 'workflow' },
        '/workflow/detail': { handler: () => App.showPage(() => {
            const hash = Router.parseHash();
            Pages.renderWorkflowDetail(hash.query.id);
        }), authRequired: true, nav: 'workflow' },
        '/monitoring': { handler: () => App.showPage(() => Pages.renderMonitoring()), authRequired: true, nav: 'monitoring' },
        '/config': { handler: () => App.showPage(() => Pages.renderConfig()), authRequired: true, nav: 'config' },
        '/batch': { handler: () => App.showPage(() => Pages.renderBatch()), authRequired: true, nav: 'batch' },
    },

    /** 初始化路由 */
    init() {
        window.addEventListener('hashchange', () => this.handleRoute());
        this.handleRoute();
    },

    /** 导航到指定路由 */
    navigate(path) {
        if (!path.startsWith('/')) path = '/' + path;
        window.location.hash = '#' + path;
    },

    /** 处理当前 hash 路由 */
    handleRoute() {
        const parsed = this.parseHash();
        let path = parsed.path;

        // 匹配路由（支持 /history/detail?id=xxx 形式）
        let route = this.routes[path];

        // 如果精确匹配失败，尝试模糊匹配
        if (!route) {
            // 尝试匹配带参数的路由（如 /history/detail）
            for (const routePath of Object.keys(this.routes)) {
                if (path.startsWith(routePath)) {
                    route = this.routes[routePath];
                    break;
                }
            }
        }

        // 默认路由
        if (!route) {
            // 访问根路径时，认证关闭或已登录都跳转到仪表盘
            if (path === '/' || path === '') {
                if (!CONFIG.AUTH_ENABLED || Auth.isLoggedIn()) {
                    this.navigate('/dashboard');
                } else {
                    this.navigate('/login');
                }
                return;
            }
            // 未知路由，显示 404
            App.showPage(() => Promise.resolve(`
                <div class="empty-state">
                    <div class="empty-icon">🔍</div>
                    <div class="mb-md">页面不存在</div>
                    <button class="btn btn-primary" onclick="Router.navigate('/dashboard')">返回首页</button>
                </div>
            `));
            return;
        }

        // 登录检查
        if (route.authRequired && !Auth.isLoggedIn()) {
            UI.toast('请先登录', 'warning');
            setTimeout(() => this.navigate('/login'), 500);
            return;
        }

        // 认证关闭时，访问登录页直接跳转到仪表盘
        if (!CONFIG.AUTH_ENABLED && path === '/login') {
            this.navigate('/dashboard');
            return;
        }

        // 已登录时访问登录页，跳转到仪表盘
        if (path === '/login' && Auth.isLoggedIn()) {
            this.navigate('/dashboard');
            return;
        }

        // 执行路由处理器
        try {
            route.handler();
        } catch (err) {
            console.error('路由处理异常:', err);
            UI.toast('页面加载失败: ' + err.message, 'error');
        }

        // 更新侧边栏激活状态
        this.updateNavActive(route.nav);
    },

    /**
     * 解析当前 hash
     * @returns {object} { path, query }
     */
    parseHash() {
        let hash = window.location.hash.slice(1); // 去掉 #
        if (!hash) return { path: '/', query: {} };

        // 分离路径和查询参数
        const [path, queryString] = hash.split('?');
        const query = {};
        if (queryString) {
            const search = new URLSearchParams(queryString);
            for (const [k, v] of search.entries()) {
                query[k] = v;
            }
        }

        return { path: path || '/', query };
    },

    /** 更新侧边栏导航激活状态 */
    updateNavActive(navKey) {
        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.remove('active');
        });
        if (navKey) {
            const activeItem = document.querySelector(`.nav-item[data-nav="${navKey}"]`);
            if (activeItem) {
                activeItem.classList.add('active');
            }
        }
    },
};

/* ============================================================
 * 7. 应用主体 — 布局与初始化
 * ============================================================ */
const App = {
    lastReviewResult: null,
    _batchTimer: null,
    _batchWebSocket: null,
    _batchPollErrors: 0,
    _batchPollInterval: 2000,

    /** 初始化应用 */
    async init() {
        // 创建根容器
        document.body.innerHTML = '<div id="app"></div>';
        // 创建 loading 遮罩
        UI._ensureLoading();
        UI.loading(false);

        // 从后端动态获取认证状态
        try {
            const health = await fetch('/api/v1/health').then(r => r.json());
            if (health.auth_enabled !== undefined) {
                CONFIG.AUTH_ENABLED = health.auth_enabled;
            }
        } catch (e) {
            // 获取失败时保持默认值
            console.warn('无法获取认证状态，使用默认配置:', e);
        }

        // 初始化路由
        Router.init();
        // 绑定全局事件
        this.bindGlobalEvents();
    },

    /**
     * 显示登录页（不含侧边栏和顶部栏）
     * @param {function} contentFn - 返回 HTML 的函数（可选）
     */
    showLogin() {
        const html = Pages.renderLogin();
        document.getElementById('app').innerHTML = html;
        Pages.bindLoginEvents();
    },

    /**
     * 显示带布局的页面（包含侧边栏和顶部栏）
     * @param {function} contentFn - 返回 HTML 字符串或 Promise<HTML字符串> 的函数
     */
    async showPage(contentFn) {
        // 渲染布局框架
        document.getElementById('app').innerHTML = `
            <div class="app-layout">
                ${this.renderSidebar()}
                <div class="main-container">
                    ${this.renderTopbar()}
                    <main class="page-content" id="app-content">
                        <div class="spinner-container">
                            <div class="spinner"></div>
                            <div class="spinner-text">加载中...</div>
                        </div>
                    </main>
                </div>
            </div>
            <div class="sidebar-overlay" id="sidebar-overlay"></div>
        `;

        // 绑定布局事件
        this.bindLayoutEvents();

        // 更新侧边栏激活状态
        const parsed = Router.parseHash();
        const route = Router.routes[parsed.path];
        if (route) {
            Router.updateNavActive(route.nav);
        }

        // 执行页面内容渲染
        try {
            await contentFn();
        } catch (err) {
            document.getElementById('app-content').innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">⚠️</div>
                    <div class="mb-md">页面加载失败：${UI.escapeHtml(err.message)}</div>
                    <button class="btn btn-primary" onclick="location.reload()">重新加载</button>
                </div>
            `;
        }
    },

    /** 渲染侧边栏 */
    renderSidebar() {
        const user = Auth.getUser();
        const navItems = [
            { key: 'dashboard', icon: '📊', text: '仪表盘', route: '/dashboard' },
            { key: 'review', icon: '✏️', text: '提交审核', route: '/review' },
            { key: 'batch', icon: '📦', text: '批量审核', route: '/batch' },
            { key: 'history', icon: '📋', text: '审核历史', route: '/history' },
            { key: 'rules', icon: '🛡️', text: '规则管理', route: '/rules' },
            { key: 'config', icon: '⚙️', text: '系统配置', route: '/config' },
        ];

        return `
            <aside class="sidebar" id="app-sidebar">
                <div class="sidebar-header">
                    <div class="sidebar-logo">
                        <span class="logo-icon">📝</span>
                        <span>GEO 审核</span>
                    </div>
                </div>
                <nav class="sidebar-nav">
                    <div class="nav-section">功能菜单</div>
                    ${navItems.map(item => `
                        <div class="nav-item" data-nav="${item.key}" data-route="${item.route}">
                            <span class="nav-icon">${item.icon}</span>
                            <span class="nav-text">${item.text}</span>
                        </div>
                    `).join('')}
                </nav>
                <div class="sidebar-footer">
                    <div>v1.0.0</div>
                    <div>© 2024 GEO 审核</div>
                </div>
            </aside>
        `;
    },

    /** 渲染顶部栏 */
    renderTopbar() {
        const user = Auth.getUser() || {};
        const initial = (user.username || user.full_name || 'U').charAt(0).toUpperCase();
        const parsed = Router.parseHash();
        const route = Router.routes[parsed.path];
        const titleMap = {
            dashboard: '仪表盘',
            review: '提交审核',
            batch: '批量审核',
            history: '审核历史',
            config: '系统配置',
        };
        const title = (route && route.nav && titleMap[route.nav]) || 'GEO 审核';

        return `
            <header class="topbar">
                <div class="topbar-left">
                    <span class="topbar-title">${UI.escapeHtml(title)}</span>
                </div>
                <div class="topbar-right">
                    <div class="user-info" id="user-menu-trigger">
                        <div class="user-avatar">${UI.escapeHtml(initial)}</div>
                        <div>
                            <div class="user-name">${UI.escapeHtml(user.full_name || user.username || '用户')}</div>
                            <div class="user-role">${UI.escapeHtml(user.role || '用户')}</div>
                        </div>
                    </div>
                    ${CONFIG.AUTH_ENABLED ? `<button class="btn btn-danger btn-sm" id="logout-btn">退出</button>` : ''}
                </div>
            </header>
        `;
    },

    /**
     * 更新内容区域
     * @param {string} html - HTML 内容
     */
    updateContent(html) {
        const el = document.getElementById('app-content');
        if (el) {
            el.style.opacity = '0';
            el.innerHTML = html;
            // 触发回流后添加淡入动画
            requestAnimationFrame(() => {
                el.style.transition = 'opacity 0.25s ease';
                el.style.opacity = '1';
            });
        }
    },

    /** 绑定全局事件（使用事件委托） */
    bindGlobalEvents() {
        // 全局点击事件处理
        document.addEventListener('click', (e) => {
            // 导航项点击
            const navItem = e.target.closest('.nav-item[data-route]');
            if (navItem) {
                e.preventDefault();
                const route = navItem.dataset.route;
                if (route) Router.navigate(route);
                // 移动端关闭侧边栏
                this._closeSidebarMobile();
                return;
            }

            // 退出按钮
            if (e.target.closest('#logout-btn')) {
                e.preventDefault();
                this._handleLogout();
                return;
            }
        });

        // ESC 关闭模态框
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                UI.closeModal();
            }
        });
    },

    /** 绑定布局事件 */
    bindLayoutEvents() {
        // 清除之前的批量定时器和 WebSocket
        if (this._batchTimer) {
            clearTimeout(this._batchTimer);
            this._batchTimer = null;
        }
        this._closeBatchWebSocket();
    },

    /** 关闭批量审核 WebSocket 连接 */
    _closeBatchWebSocket() {
        if (this._batchWebSocket) {
            try {
                this._batchWebSocket.close();
            } catch (e) {}
            this._batchWebSocket = null;
        }
    },

    /**
     * 启动批量进度追踪：WebSocket 优先，失败则指数退避轮询
     * @param {string} batchId - 批量任务 ID
     * @param {function} onUpdate - 进度更新回调
     */
    _startBatchProgressTracking(batchId, onUpdate) {
        if (this._batchTimer) {
            clearTimeout(this._batchTimer);
            this._batchTimer = null;
        }
        this._closeBatchWebSocket();

        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${window.location.host}/api/v1/review/batch/${batchId}/ws`;

        try {
            const ws = new WebSocket(wsUrl);
            this._batchWebSocket = ws;
            this._batchPollErrors = 0;
            this._batchPollInterval = 2000;

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.error) {
                        UI.toast(data.error, 'error');
                        this._closeBatchWebSocket();
                        return;
                    }
                    onUpdate(data);
                    this._batchPollErrors = 0;
                } catch (e) {}
            };

            ws.onerror = () => {
                this._closeBatchWebSocket();
                this._startBatchPolling(batchId, onUpdate);
            };

            ws.onclose = () => {
                this._batchWebSocket = null;
            };

            setTimeout(() => {
                if (ws.readyState === WebSocket.CONNECTING) {
                    ws.close();
                    this._batchWebSocket = null;
                    this._startBatchPolling(batchId, onUpdate);
                }
            }, 3000);

        } catch (e) {
            this._closeBatchWebSocket();
            this._startBatchPolling(batchId, onUpdate);
        }
    },

    /**
     * 指数退避轮询兜底
     * @param {string} batchId - 批量任务 ID
     * @param {function} onUpdate - 进度更新回调
     */
    _startBatchPolling(batchId, onUpdate) {
        const maxErrors = 10;
        const maxInterval = 15000;

        const poll = async () => {
            try {
                const newProgress = await ApiClient.get(`/review/batch/${batchId}/progress`);
                this._batchPollErrors = 0;
                this._batchPollInterval = 2000;
                onUpdate(newProgress);
            } catch (err) {
                this._batchPollErrors++;

                if (this._batchPollErrors === 3) {
                    UI.toast('进度更新连接不稳定，正在重试...', 'warning');
                }

                if (this._batchPollErrors >= maxErrors) {
                    UI.toast('进度查询失败，请手动刷新', 'error');
                    return;
                }

                this._batchPollInterval = Math.min(
                    this._batchPollInterval * 1.5,
                    maxInterval
                );
            }

            this._batchTimer = setTimeout(poll, this._batchPollInterval);
        };

        this._batchTimer = setTimeout(poll, this._batchPollInterval);
    },

    /** 处理登出 */
    async _handleLogout() {
        if (!CONFIG.AUTH_ENABLED) return;
        const confirmed = await UI.confirm('确定要退出登录吗？', { confirmText: '退出', danger: true });
        if (!confirmed) return;

        await Auth.logout();
        UI.toast('已退出登录', 'info');
    },

    /** 移动端关闭侧边栏 */
    _closeSidebarMobile() {
        const sidebar = document.getElementById('app-sidebar');
        const overlay = document.getElementById('sidebar-overlay');
        if (sidebar) sidebar.classList.remove('show');
        if (overlay) overlay.classList.remove('show');
    },
};

/* ============================================================
 * 8. 启动应用
 * ============================================================ */
document.addEventListener('DOMContentLoaded', () => {
    App.init();
});
