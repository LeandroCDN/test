import { NavLink, Outlet } from "react-router-dom";

export default function App() {
  return (
    <>
      <nav className="top-nav">
        <div className="top-nav-inner">
          <NavLink to="/" end className={({ isActive }) => `top-nav-link${isActive ? " active" : ""}`}>
            Bot 1
          </NavLink>
          <NavLink to="/bot-2" className={({ isActive }) => `top-nav-link${isActive ? " active" : ""}`}>
            Bot 2
          </NavLink>
        </div>
      </nav>
      <Outlet />
    </>
  );
}
