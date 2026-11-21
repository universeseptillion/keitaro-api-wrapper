import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { catchError, Observable, throwError } from 'rxjs';

import { env } from '../settings/settings';

export interface Offer {
    id: number;
    name: string;
    state: string;
}

export interface StreamOffer {
    offer_id: number;
    name: string;
    share: number;
    removed: boolean;
    pinned: boolean;
}

export interface EditorStream {
    id: number;
    name: string;
    dirty: boolean;
    offers: StreamOffer[];
}

export interface CreateCampaignRequest {
    name: string;
    country: string;
    offer_id: number;
}

export interface CreateCampaignResponse {
    id: number;
    name: string;
    google_stream_id: number;
    offer_stream_id: number;
}

export interface FetchStreamsResponse {
    campaign_id: number;
    streams: EditorStream[];
}

export type StreamAction =
    | 'add'
    | 'remove'
    | 'restore'
    | 'pin'
    | 'unpin'
    | 'cancel';

@Injectable({
    providedIn: 'root'
})
export class KeitaroService {
    private readonly http = inject(HttpClient);

    private readonly apiUrl =
        `${env.SERVER_URL.replace(/\/+$/, '')}/api`;

    getOffers(): Observable<Offer[]> {
        return this.http.get<Offer[]>(`${this.apiUrl}/offers`).pipe(
            catchError(error =>
                this.handleError(error, 'Не удалось загрузить офферы')
            )
        );
    }

    createCampaign(
        body: CreateCampaignRequest
    ): Observable<CreateCampaignResponse> {
        return this.http.post<CreateCampaignResponse>(
            `${this.apiUrl}/campaigns`,
            body
        ).pipe(
            catchError(error =>
                this.handleError(
                    error,
                    'Не удалось создать кампанию. Проверьте Keitaro перед повторной отправкой'
                )
            )
        );
    }

    fetchStreams(campaignId: number): Observable<FetchStreamsResponse> {
        return this.http.post<FetchStreamsResponse>(
            `${this.apiUrl}/campaigns/${campaignId}/fetch-streams`,
            {}
        ).pipe(
            catchError(error =>
                this.handleError(error, 'Не удалось загрузить потоки')
            )
        );
    }

    editStream(
        streamId: number,
        action: StreamAction,
        offerId?: number
    ): Observable<EditorStream> {
        return this.http.post<EditorStream>(
            `${this.apiUrl}/streams/${streamId}/actions`,
            {
                action,
                offer_id: offerId
            }
        ).pipe(
            catchError(error =>
                this.handleError(error, 'Не удалось изменить поток')
            )
        );
    }

    pushStream(streamId: number): Observable<EditorStream> {
        return this.http.post<EditorStream>(
            `${this.apiUrl}/streams/${streamId}/push`,
            {}
        ).pipe(
            catchError(error =>
                this.handleError(
                    error,
                    'Не удалось подтвердить публикацию. Проверьте поток в Keitaro'
                )
            )
        );
    }

    private handleError(
        error: HttpErrorResponse,
        fallback: string
    ): Observable<never> {
        let message = fallback;

        if (error.status === 0) {
            message = `Нет соединения с сервером. ${fallback}`;
        } else {
            const detail: unknown = error.error?.detail;

            if (typeof detail === 'string') {
                message = detail;
            } else if (Array.isArray(detail)) {
                const messages = detail
                    .map((item: unknown) => {
                        if (
                            typeof item === 'object' &&
                            item !== null &&
                            'msg' in item &&
                            typeof item.msg === 'string'
                        ) {
                            return item.msg;
                        }

                        return '';
                    })
                    .filter(Boolean);

                if (messages.length) {
                    message = messages.join('; ');
                }
            }
        }

        return throwError(() => new Error(message));
    }
}