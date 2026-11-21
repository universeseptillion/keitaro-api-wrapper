import { CommonModule } from '@angular/common';
import {
  ChangeDetectorRef,
  Component,
  inject,
  OnInit
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import {
  EditorStream,
  KeitaroService,
  Offer,
  StreamAction
} from '../services/keitaro.service';

@Component({
  selector: 'keitaro-api-wrapper',
  imports: [CommonModule, FormsModule],
  templateUrl: './keitaro-api-wrapper.component.html',
  styleUrl: './keitaro-api-wrapper.component.css'
})
export class KeitaroAPIWrapperComponent implements OnInit {
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly keitaroService = inject(KeitaroService);

  campaignName = '';
  country = '';
  search = '';

  offers: Offer[] = [];
  selectedOffer: Offer | null = null;

  loading = false;
  error = '';

  creating = false;
  createError = '';
  createdCampaignId: number | null = null;

  editorCampaignId: number | null = null;

  streams: EditorStream[] = [];
  fetchingStreams = false;
  streamsError = '';
  streamsLoaded = false;

  streamSearch: Record<number, string> = {};
  streamErrors: Record<number, string> = {};
  streamBusy: Record<number, boolean> = {};

  ngOnInit(): void {
    void this.loadOffers();
  }

  get filteredOffers(): Offer[] {
    const query = this.search.trim().toLowerCase();

    if (!query) {
      return [];
    }

    return this.offers
      .filter(offer =>
        String(offer.id).includes(query) ||
        offer.name.toLowerCase().includes(query)
      )
      .slice(0, 30);
  }

  selectOffer(offer: Offer): void {
    this.selectedOffer = offer;
    this.search = '';
  }

  async loadOffers(): Promise<void> {
    if (this.loading) {
      return;
    }

    this.loading = true;
    this.error = '';

    try {
      this.offers = await firstValueFrom(
        this.keitaroService.getOffers()
      );
    } catch (error) {
      this.error = error instanceof Error
        ? error.message
        : 'Ошибка загрузки офферов';
    } finally {
      this.loading = false;
      this.cdr.markForCheck();
    }
  }

  async createCampaign(): Promise<void> {
    if (this.creating) {
      return;
    }

    this.createError = '';
    this.createdCampaignId = null;

    const name = this.campaignName.trim();
    const country = this.country.trim().toUpperCase();
    const offer = this.selectedOffer;

    if (!name || !/^[A-Z]{2}$/.test(country) || !offer) {
      this.createError =
        'Введите название, двухбуквенный код страны и выберите оффер.';
      return;
    }

    this.creating = true;

    try {
      const data = await firstValueFrom(
        this.keitaroService.createCampaign({
          name,
          country,
          offer_id: offer.id
        })
      );

      this.createdCampaignId = data.id;
      this.editorCampaignId = data.id;
    } catch (error) {
      this.createError = error instanceof Error
        ? error.message
        : 'Ошибка создания кампании';
    } finally {
      this.creating = false;
      this.cdr.markForCheck();
    }
  }

  async fetchStreams(): Promise<void> {
    if (this.fetchingStreams || this.editingStream) {
      return;
    }

    this.streamsError = '';

    const campaignId = Number(this.editorCampaignId);

    if (!Number.isSafeInteger(campaignId) || campaignId <= 0) {
      this.streamsError =
        'Введите положительный целочисленный ID кампании.';
      return;
    }

    this.fetchingStreams = true;

    try {
      const data = await firstValueFrom(
        this.keitaroService.fetchStreams(campaignId)
      );

      this.streams = data.streams;
      this.streamsLoaded = true;
      this.streamSearch = {};
      this.streamErrors = {};
      this.streamBusy = {};
    } catch (error) {
      this.streamsError = error instanceof Error
        ? error.message
        : 'Ошибка загрузки потоков';
    } finally {
      this.fetchingStreams = false;
      this.cdr.markForCheck();
    }
  }

  get editingStream(): boolean {
    return Object.values(this.streamBusy).some(Boolean);
  }

  findStreamOffers(stream: EditorStream): Offer[] {
    const query = (this.streamSearch[stream.id] ?? '')
      .trim()
      .toLowerCase();

    if (!query) {
      return [];
    }

    const existingIds = new Set(
      stream.offers.map(offer => offer.offer_id)
    );

    return this.offers
      .filter(offer =>
        offer.state === 'active' &&
        !existingIds.has(offer.id) &&
        (
          String(offer.id).includes(query) ||
          offer.name.toLowerCase().includes(query)
        )
      )
      .slice(0, 20);
  }

  async streamAction(
    stream: EditorStream,
    action: StreamAction,
    offerId?: number
  ): Promise<void> {
    if (this.streamBusy[stream.id] || this.fetchingStreams) {
      return;
    }

    this.streamBusy[stream.id] = true;
    this.streamErrors[stream.id] = '';

    try {
      const updatedStream = await firstValueFrom(
        this.keitaroService.editStream(stream.id, action, offerId)
      );

      this.streams = this.streams.map(item =>
        item.id === updatedStream.id ? updatedStream : item
      );

      if (action === 'add' || action === 'cancel') {
        this.streamSearch[stream.id] = '';
      }
    } catch (error) {
      this.streamErrors[stream.id] = error instanceof Error
        ? error.message
        : 'Ошибка изменения потока';
    } finally {
      this.streamBusy[stream.id] = false;
      this.cdr.markForCheck();
    }
  }

  async pushStream(stream: EditorStream): Promise<void> {
    if (
      this.streamBusy[stream.id] ||
      this.fetchingStreams ||
      !stream.dirty
    ) {
      return;
    }

    this.streamBusy[stream.id] = true;
    this.streamErrors[stream.id] = '';

    try {
      const updatedStream = await firstValueFrom(
        this.keitaroService.pushStream(stream.id)
      );

      this.streams = this.streams.map(item =>
        item.id === updatedStream.id ? updatedStream : item
      );
    } catch (error) {
      this.streamErrors[stream.id] = error instanceof Error
        ? error.message
        : 'Ошибка публикации потока';
    } finally {
      this.streamBusy[stream.id] = false;
      this.cdr.markForCheck();
    }
  }

  trackStream(_: number, stream: EditorStream): number {
    return stream.id;
  }
}