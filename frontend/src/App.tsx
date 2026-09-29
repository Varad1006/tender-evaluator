import { Alert } from '@mui/material';
import { Navigate, Outlet, Route, Routes, useParams } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import { TenderProvider, useTender } from './hooks/useTender';
import { BiddersPage } from './pages/BiddersPage';
import { ComparisonPage } from './pages/ComparisonPage';
import { CriteriaPage } from './pages/CriteriaPage';
import { TenderListPage } from './pages/TenderListPage';
import { VerdictsPage } from './pages/VerdictsPage';

function TenderShell() {
  const { tender, error } = useTender();
  return (
    <AppShell tender={tender}>
      {error && !tender ? <Alert severity="error">{error}</Alert> : <Outlet />}
    </AppShell>
  );
}

function TenderLayout() {
  const { tenderId = '' } = useParams();
  return (
    <TenderProvider tenderId={tenderId}>
      <TenderShell />
    </TenderProvider>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<TenderListPage />} />
      <Route path="/tenders/:tenderId" element={<TenderLayout />}>
        <Route index element={<Navigate to="criteria" replace />} />
        <Route path="criteria" element={<CriteriaPage />} />
        <Route path="bidders" element={<BiddersPage />} />
        <Route path="verdicts" element={<VerdictsPage />} />
        <Route path="comparison" element={<ComparisonPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
