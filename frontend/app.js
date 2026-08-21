class PhoneAssistant {
    constructor() {
        this.activeCalls = {};
        this.selectedCallId = null;
        this.pollInterval = null;

        this.dialNumber = document.getElementById('dialNumber');
        this.btnCall = document.getElementById('btnCall');
        this.btnBackspace = document.getElementById('btnBackspace');
        this.callsList = document.getElementById('callsList');
        this.detailPanel = document.getElementById('detailPanel');
        this.detailNumber = document.getElementById('detailNumber');
        this.detailStatus = document.getElementById('detailStatus');
        this.detailDirection = document.getElementById('detailDirection');
        this.detailFrom = document.getElementById('detailFrom');
        this.detailTo = document.getElementById('detailTo');
        this.detailDuration = document.getElementById('detailDuration');
        this.detailTranscript = document.getElementById('detailTranscript');
        this.btnHangup = document.getElementById('btnHangup');
        this.btnBack = document.getElementById('btnBack');
        this.settingsModal = document.getElementById('settingsModal');
        this.btnCloseSettings = document.getElementById('btnCloseSettings');
        this.btnSaveSettings = document.getElementById('btnSaveSettings');
        this.toast = document.getElementById('toast');

        this.init();
    }

    init() {
        // Dial pad buttons
        document.querySelectorAll('.dial-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const digit = btn.dataset.digit;
                this.dialNumber.value += digit;
                this.dialNumber.focus();
            });
        });

        // Call button
        this.btnCall.addEventListener('click', () => this.toggleCall());

        // Backspace
        this.btnBackspace.addEventListener('click', () => {
            this.dialNumber.value = this.dialNumber.value.slice(0, -1);
        });

        // Keyboard input
        this.dialNumber.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this.toggleCall();
            }
        });

        // Detail panel back button
        this.btnBack.addEventListener('click', () => this.hideDetail());

        // Hangup button
        this.btnHangup.addEventListener('click', () => this.hangupSelected());

        // Settings
        this.btnCloseSettings.addEventListener('click', () => this.settingsModal.classList.add('hidden'));
        this.btnSaveSettings.addEventListener('click', () => this.saveSettings());

        // Load config and start polling
        this.loadConfig();
        this.startPolling();
    }

    async loadConfig() {
        try {
            const res = await fetch('/api/config');
            const data = await res.json();
            document.getElementById('settingsFromNumber').value = data.phoneNumber;
        } catch (e) {
            console.error('Failed to load config:', e);
        }
    }

    startPolling() {
        this.pollInterval = setInterval(() => this.pollCalls(), 3000);
        this.pollCalls();
    }

    async pollCalls() {
        try {
            const res = await fetch('/api/calls/active');
            const data = await res.json();
            if (data.ok) {
                this.updateCallsList(data.calls);
            }
        } catch (e) {
            console.error('Poll error:', e);
        }
    }

    updateCallsList(calls) {
        const callsArray = Array.isArray(calls) ? calls : [];

        // Update active calls map
        const currentIds = new Set(callsArray.map(c => c.callId));
        for (const id of Object.keys(this.activeCalls)) {
            if (!currentIds.has(id)) {
                delete this.activeCalls[id];
            }
        }
        for (const call of callsArray) {
            this.activeCalls[call.callId] = call;
        }

        // Render
        if (callsArray.length === 0) {
            this.callsList.innerHTML = '<div class="no-calls">No active calls</div>';
            return;
        }

        this.callsList.innerHTML = callsArray.map(call => {
            const number = call.direction === 'inbound' ? call.from : call.to;
            const direction = call.direction || 'outbound';
            const status = call.status || 'active';
            const elapsed = call.startTime ? this.formatDuration(Date.now() / 1000 - call.startTime) : '0:00';
            const isSelected = call.callId === this.selectedCallId;

            return `
                <div class="call-card ${isSelected ? 'selected' : ''}" data-call-id="${call.callId}">
                    <div class="call-icon ${direction}">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            ${direction === 'inbound'
                                ? '<path d="M15.05 5A5 5 0 0 1 19 8.95M15.05 1A9 9 0 0 1 23 8.94M16 16l-4-3V8"/>'
                                : '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/>'
                            }
                        </svg>
                    </div>
                    <div class="call-info">
                        <div class="call-number">${this.formatNumber(number)}</div>
                        <div class="call-meta">${direction} &middot; ${elapsed}</div>
                    </div>
                    <span class="call-status-badge ${status}">${status}</span>
                </div>
            `;
        }).join('');

        // Attach click handlers
        this.callsList.querySelectorAll('.call-card').forEach(card => {
            card.addEventListener('click', () => {
                this.showDetail(card.dataset.callId);
            });
        });

        // Update detail panel if visible
        if (this.selectedCallId && this.activeCalls[this.selectedCallId]) {
            this.updateDetail();
        }
    }

    async toggleCall() {
        const number = this.dialNumber.value.trim();
        if (!number) return;

        if (this.btnCall.classList.contains('calling')) {
            // Hangup current call
            const callId = Object.keys(this.activeCalls).find(id =>
                this.activeCalls[id].to === number || this.activeCalls[id].from === number
            );
            if (callId) {
                await this.hangupCall(callId);
            }
        } else {
            await this.makeCall(number);
        }
    }

    async makeCall(number) {
        try {
            this.btnCall.disabled = true;
            this.btnCall.innerHTML = `
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/>
                </svg>
                Calling...
            `;
            this.btnCall.classList.add('calling');

            const res = await fetch('/api/calls', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ to: number }),
            });
            const data = await res.json();

            if (data.ok) {
                this.showToast('Call initiated');
                this.dialNumber.value = '';
            } else {
                this.showToast('Failed to place call: ' + (data.detail || 'Unknown error'), true);
            }
        } catch (e) {
            console.error('Make call error:', e);
            this.showToast('Failed to place call', true);
        } finally {
            this.btnCall.disabled = false;
            this.btnCall.classList.remove('calling');
            this.btnCall.innerHTML = `
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/>
                </svg>
                Call
            `;
        }
    }

    async hangupCall(callId) {
        try {
            const res = await fetch(`/api/calls/${callId}/hangup`, { method: 'POST' });
            const data = await res.json();
            if (data.ok) {
                this.showToast('Call ended');
            }
        } catch (e) {
            console.error('Hangup error:', e);
        }
    }

    hangupSelected() {
        if (this.selectedCallId) {
            this.hangupCall(this.selectedCallId);
        }
    }

    showDetail(callId) {
        this.selectedCallId = callId;
        this.detailPanel.classList.remove('hidden');
        this.updateDetail();

        // Highlight selected card
        this.callsList.querySelectorAll('.call-card').forEach(card => {
            card.classList.toggle('selected', card.dataset.callId === callId);
        });
    }

    hideDetail() {
        this.selectedCallId = null;
        this.detailPanel.classList.add('hidden');
        this.callsList.querySelectorAll('.call-card').forEach(card => {
            card.classList.remove('selected');
        });
    }

    updateDetail() {
        const call = this.activeCalls[this.selectedCallId];
        if (!call) return;

        this.detailNumber.textContent = this.formatNumber(call.direction === 'inbound' ? call.from : call.to);
        this.detailStatus.textContent = call.status || 'active';
        this.detailDirection.textContent = call.direction || 'outbound';
        this.detailFrom.textContent = call.from || '-';
        this.detailTo.textContent = call.to || '-';

        const elapsed = call.startTime ? this.formatDuration(Date.now() / 1000 - call.startTime) : '0:00';
        this.detailDuration.textContent = elapsed;

        // Build transcript from call data
        let transcriptHtml = '';
        if (call.lastUserSpeech) {
            transcriptHtml += `<div class="transcript-entry user"><div class="role">Caller</div>${this.escapeHtml(call.lastUserSpeech)}</div>`;
        }
        if (call.lastAIReply) {
            transcriptHtml += `<div class="transcript-entry ai"><div class="role">AI Assistant</div>${this.escapeHtml(call.lastAIReply)}</div>`;
        }
        if (!transcriptHtml) {
            transcriptHtml = '<div class="transcript-placeholder">Conversation will appear here...</div>';
        }
        this.detailTranscript.innerHTML = transcriptHtml;
    }

    formatNumber(num) {
        if (!num) return 'Unknown';
        const clean = num.replace(/\D/g, '');
        if (clean.length === 10) {
            return `+1 (${clean.slice(0, 3)}) ${clean.slice(3, 6)}-${clean.slice(6)}`;
        }
        return num;
    }

    formatDuration(seconds) {
        const s = Math.max(0, Math.floor(seconds));
        const m = Math.floor(s / 60);
        const sec = s % 60;
        return `${m}:${sec.toString().padStart(2, '0')}`;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    showToast(message, isError = false) {
        this.toast.textContent = message;
        this.toast.className = `toast ${isError ? 'error' : ''}`;
        clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(() => {
            this.toast.classList.add('hidden');
        }, 3000);
    }

    saveSettings() {
        this.settingsModal.classList.add('hidden');
        this.showToast('Settings saved');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new PhoneAssistant();
});
