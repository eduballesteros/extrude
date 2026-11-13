/**
 * Último proyecto convertido en este navegador, para reabrirlo tras recargar la página sin
 * volver a subir el plano. Solo se guarda una referencia (id del trabajo en el servidor, nombre
 * y servidor): el modelo, el YAML y las revisiones siguen en el servidor.
 */
const KEY = 'extrude.recentProject';

export interface RecentProject {
  job: string;
  /** Nombre del plano sin extensión (también el de las vistas e imágenes del proyecto). */
  name: string;
  server: string;
  savedAt: string;
}

export function loadRecent(server: string): RecentProject | null {
  try {
    const data = JSON.parse(localStorage.getItem(KEY) ?? 'null') as Partial<RecentProject> | null;
    if (
      !data ||
      typeof data.job !== 'string' ||
      !/^[0-9a-f]{32}$/.test(data.job) ||
      typeof data.name !== 'string' ||
      data.server !== server
    ) {
      return null;
    }
    return {
      job: data.job,
      name: data.name,
      server,
      savedAt: typeof data.savedAt === 'string' ? data.savedAt : '',
    };
  } catch {
    return null;
  }
}

export function saveRecent(project: Omit<RecentProject, 'savedAt'>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...project, savedAt: new Date().toISOString() }));
  } catch {
    // Sin almacenamiento: no se podrá reabrir tras recargar
  }
}

export function forgetRecent(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // nada que olvidar
  }
}
