/** Carga por arrastrar y soltar sobre `target` y por selector de archivo. */
export function bindFileInputs(
  target: HTMLElement,
  input: HTMLInputElement,
  onFile: (file: File) => void,
): void {
  input.addEventListener('change', () => {
    const file = input.files?.[0];
    if (file) onFile(file);
    input.value = '';
  });
  target.addEventListener('dragover', (event) => {
    event.preventDefault();
    target.classList.add('dragging');
  });
  target.addEventListener('dragleave', () => target.classList.remove('dragging'));
  target.addEventListener('drop', (event) => {
    event.preventDefault();
    target.classList.remove('dragging');
    const file = event.dataTransfer?.files[0];
    if (file) onFile(file);
  });
}
