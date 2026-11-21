import { Component, signal } from '@angular/core';
import { KeitaroAPIWrapperComponent } from './keitaro-api-wrapper/keitaro-api-wrapper.component';

@Component({
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
  imports: [KeitaroAPIWrapperComponent],
})
export class App {
  protected readonly title = signal('frontend');
}
