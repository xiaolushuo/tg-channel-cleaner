import { useEffect } from "react";
import { useApp } from "@/store/AppContext";
import { MainWindow } from "@/components/main/MainWindow";

export function App() {
  const { bootstrap } = useApp();
  useEffect(() => { bootstrap(); }, [bootstrap]);
  return <MainWindow />;
}
