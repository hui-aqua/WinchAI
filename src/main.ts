import './styles/main.css';
import { initializeApp } from '@/app/initializeApp';

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeApp, { once: true });
} else {
    initializeApp();
}
