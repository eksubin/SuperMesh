import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { MeshProvider } from './state/MeshProvider';
import './styles/app.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MeshProvider>
      <App />
    </MeshProvider>
  </StrictMode>,
);
